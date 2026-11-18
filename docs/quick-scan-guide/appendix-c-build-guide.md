# Appendix C. Build This Guide (1 minute)

> **TL;DR:** Compile the quick-scan guide into Word (.docx) or PDF format using Pandoc. Headings are already structured for clean rendering.

---

## C.1 Prerequisites

Pandoc is installed on macOS via Homebrew:
```bash
brew install pandoc
```

---

## C.2 Build Commands

### 1. Build Microsoft Word Document (`.docx`)
Run from the root directory:
```bash
pandoc docs/quick-scan-guide/quick-scan-guide.md \
  -o docs/quick-scan-guide/Quick_Scan_Guide.docx \
  --toc --toc-depth=2
```

### 2. Build PDF via XeLaTeX
```bash
pandoc docs/quick-scan-guide/quick-scan-guide.md \
  -o docs/quick-scan-guide/Quick_Scan_Guide.pdf \
  --toc --toc-depth=2 \
  --pdf-engine=/Library/TeX/texbin/xelatex \
  -V geometry:margin=2.2cm \
  -V fontsize=11pt \
  -V colorlinks=true
```

> **Readability Tip:** For the best visual experience in Word or PDF, use a modern sans-serif font (e.g. Inter, Helvetica Neue, or Calibri), 1.15 to 1.25 line spacing, and left-aligned text.
