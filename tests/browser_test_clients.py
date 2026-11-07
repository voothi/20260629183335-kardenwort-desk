"""Finish owned route handlers and await browser closure before stopping HTTP."""
from functools import wraps
from playwright._impl._errors import TargetClosedError


class BrowserClients:
    def __init__(self, browser, monkeypatch):
        self.contexts = []
        self.pending = {}
        self.closed = set()
        self.cancelled = set()
        self.errors = []
        self.handlers = {}
        self.unregister = {}
        self.monkeypatch = monkeypatch
        create = browser.new_context

        def new_context(*args, **kwargs):
            return self.context(create(*args, **kwargs))

        monkeypatch.setattr(browser, 'new_context', new_context)
        for context in browser.contexts:
            self.context(context)

    def context(self, context):
        self.contexts.append(context)
        self.routes(context)
        context.on('page', self.page)
        context.on('close', lambda: self.closed.add(context))
        for page in context.pages:
            self.page(page)
        close = context.close

        def close_context(*args, **kwargs):
            if context in self.closed:
                return
            for page in list(context.pages):
                self.attempt(page.close)
            self.attempt(lambda: self.cancel_routes(context))
            self.attempt(lambda: context.unroute_all(behavior='wait'))
            return close(*args, **kwargs)

        self.monkeypatch.setattr(context, 'close', close_context)
        return context

    def page(self, page):
        self.routes(page)
        close = page.close

        def close_page(*args, **kwargs):
            if page.is_closed():
                return
            self.attempt(lambda: page.evaluate('''() => {
                if (window.stopSelectionBridge && !stopSelectionBridge())
                    throw new Error('Owned native subscription did not stop');
                if (window.SSEBridge) SSEBridge.disconnect();
            }'''))
            self.attempt(lambda: self.cancel_routes(page))
            self.attempt(lambda: page.unroute_all(behavior='wait'))
            with page.expect_event('close'):
                return close(*args, **kwargs)

        self.monkeypatch.setattr(page, 'close', close_page)

    def routes(self, owner):
        self.pending[owner] = set()
        self.handlers[owner] = []
        self.unregister[owner] = owner.unroute
        register = owner.route

        def route(url, handler, **kwargs):
            @wraps(handler)
            def tracked(*args):
                self.track(owner, args[0])
                try:
                    return handler(*args)
                except BaseException as error:
                    self.route_error(args[0], error)
                    raise

            self.handlers[owner].append((handler, tracked))
            return register(url, tracked, **kwargs)

        self.monkeypatch.setattr(owner, 'route', route)
        self.monkeypatch.setattr(owner, 'unroute', lambda url, handler=None: self.unroute(owner, url, handler))

    def unroute(self, owner, url, handler):
        if handler is None:
            return self.unregister[owner](url)
        callbacks = [wrapped for original, wrapped in self.handlers[owner] if original is handler]
        if not callbacks:
            return self.unregister[owner](url, handler)
        for wrapped in callbacks:
            self.unregister[owner](url, wrapped)

    def track(self, owner, route):
        self.pending[owner].add(route)
        for name in ('fulfill', 'abort', 'continue_', 'fallback'):
            action = getattr(route, name)

            def complete(*args, action=action, **kwargs):
                result = action(*args, **kwargs)
                self.pending[owner].discard(route)
                return result

            self.monkeypatch.setattr(route, name, complete)

    def cancel_routes(self, owner):
        for route in list(self.pending[owner]):
            self.cancelled.add(route)
            route.abort('aborted')
        assert not self.pending[owner], 'Owned browser routes did not finish'

    def route_error(self, route, error):
        if isinstance(error, TargetClosedError) and route in self.cancelled:
            return
        self.errors.append(error)

    def attempt(self, action):
        try:
            action()
        except BaseException as error:
            self.errors.append(error)

    def close(self):
        for context in self.contexts:
            if context not in self.closed:
                self.attempt(context.close)
        if self.errors:
            errors, self.errors = self.errors, []
            raise BaseExceptionGroup('Owned browser route/disposal failures', errors)
