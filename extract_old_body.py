import subprocess

out = subprocess.check_output(['git', 'show', '0104c09:kardenwort_desk.py'], text=True, encoding='utf-8')
target = 'html_page = """<!DOCTYPE html>'
idx = out.find(target)
if idx != -1:
    b_idx = out.find('<body', idx)
    end_b = out.find('</body>', b_idx)
    body_html = out[b_idx:end_b+7]
    with open('old_body.html', 'w', encoding='utf-8') as f:
        f.write(body_html)
    print("Found and saved old body, length:", len(body_html))
else:
    print("target not found")
