# Appendix C. Building This Document

## C.1 Toolchain Prerequisites

The technical documentation can be compiled into distribution-ready Microsoft Word (`.docx`) and Adobe PDF (`.pdf`) formats using **Pandoc** and **XeLaTeX** **[Confirmed]**:

- **Pandoc:** Version 3.1+ (tested with Pandoc 3.10.1).
- **LaTeX Engine (for PDF):** XeLaTeX (part of MacTeX on macOS or `texlive-xetex` on Ubuntu/Debian).

Verify toolchain availability:
```bash
pandoc --version
xelatex --version
```

---

## C.2 Microsoft Word Compilation (.docx)

To compile the consolidated technical documentation into a polished Microsoft Word document with an automatic Table of Contents:

```bash
pandoc docs/technical-docs/technical-documentation.md \
  -o docs/technical-docs/Voice_AI_Platform_Docs.docx \
  --toc --toc-depth=3
```

> **Important Note:** Do **not** pass `--number-sections` to Pandoc, because the section headings throughout this document are already explicitly numbered in the markdown source.

### Custom Styling with Reference Template

To apply branded corporate styling (custom typography, header/footer logos, corporate color schemes, and margins), pass a reference Word document:

```bash
pandoc docs/technical-docs/technical-documentation.md \
  -o docs/technical-docs/Voice_AI_Platform_Docs.docx \
  --toc --toc-depth=3 \
  --reference-doc=docs/templates/corporate-style.docx
```

---

## C.3 Adobe PDF Compilation (.pdf)

To render the document as a typeset PDF with hyperlinked references, custom margins, and optimized typography:

```bash
pandoc docs/technical-docs/technical-documentation.md \
  -o docs/technical-docs/Voice_AI_Platform_Docs.pdf \
  --toc --toc-depth=3 \
  --pdf-engine=xelatex \
  -V geometry:margin=2.2cm \
  -V fontsize=10pt \
  -V colorlinks=true \
  -V linkcolor=blue \
  -V urlcolor=blue
```

---

## C.4 Building Individual Modular Chapters

Each modular section file in `docs/technical-docs/` is completely standalone and valid markdown. Individual chapters can be compiled separately for specialized audience reviews (e.g. sending only Section 9 to security auditors):

```bash
# Compile Security chapter only:
pandoc docs/technical-docs/09-security.md \
  -o docs/technical-docs/Voice_AI_Security_Review.docx \
  --toc --toc-depth=2
```
