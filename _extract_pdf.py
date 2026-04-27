import pypdf

r = pypdf.PdfReader("Quant.pdf")
with open("quant_text.txt", "w", encoding="utf-8") as f:
    for page in r.pages:
        text = page.extract_text()
        if text:
            f.write(text + "\n")
print("Done")