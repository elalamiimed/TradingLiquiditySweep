# -*- coding: utf-8 -*-
with open("visualize_detailed.py", "r", encoding="utf-8") as f:
    c = f.read()

old = 'js_data + """;'
new = 'js_data + f""";'
c = c.replace(old, new)

with open("visualize_detailed.py", "w", encoding="utf-8") as f:
    f.write(c)
print("Done -", old, "->", new)
