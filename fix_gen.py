# -*- coding: utf-8 -*-
"""Revert f-string fix and apply post-processing instead."""
with open("visualize_detailed.py", "r", encoding="utf-8") as f:
    c = f.read()

# Revert: change f"""</div></div> back to """</div></div>
c = c.replace('html_parts.append(f"""</div></div>', 'html_parts.append("""</div></div>')

# Revert: change js_data + f"""; back to js_data + """;
c = c.replace('js_data + f""";', 'js_data + """;')

# Add post-processing: after the second append, replace {{ and }}
# Find the line that writes the output
old_write = '''out_path.write_text("".join(html_parts), encoding="utf-8")'''
new_write = '''final = "".join(html_parts)
# Fix double braces in JS section: {{ -> { and }} -> }
final = final.replace("{{", "{").replace("}}", "}")
out_path.write_text(final, encoding="utf-8")'''
c = c.replace(old_write, new_write)

with open("visualize_detailed.py", "w", encoding="utf-8") as f:
    f.write(c)
print("Reverted f-string + added post-processing fix")
