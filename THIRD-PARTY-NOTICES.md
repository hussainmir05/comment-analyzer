# Third-party notices

Comment Analyzer is built on the following software and models. Each keeps its own licence.

## Bundled with the app

| Component | Licence | Notes |
|---|---|---|
| Qt 6 and PySide6 | LGPL-3.0 | Linked dynamically: the Qt libraries in the install folder can be replaced with your own build. Source: https://download.qt.io and https://code.qt.io/cgit/pyside/pyside-setup.git |
| Qt WebEngine (Chromium) | LGPL-3.0, plus Chromium's own licences (mostly BSD-style) | https://doc.qt.io/qt-6/qtwebengine-licensing.html |
| Python 3.14 | PSF License | https://docs.python.org/3/license.html |
| ONNX Runtime | MIT | https://github.com/microsoft/onnxruntime |
| tokenizers (Hugging Face) | Apache-2.0 | https://github.com/huggingface/tokenizers |
| fast-langdetect | MIT | https://github.com/LlmKira/fast-langdetect |
| fastText language identification model (lid.176.ftz) | CC BY-SA 3.0 | By Facebook AI Research, https://fasttext.cc/docs/en/language-identification.html |
| plotly.js | MIT | https://github.com/plotly/plotly.js |
| matplotlib | Matplotlib License (PSF-based) | https://matplotlib.org/stable/project/license.html |
| NumPy | BSD-3-Clause | https://numpy.org |
| python-docx | MIT | https://github.com/python-openxml/python-docx |
| openpyxl | MIT | https://foss.heptapod.net/openpyxl/openpyxl |
| httpx2 | BSD-3-Clause | https://github.com/pydantic/httpx2 |
| keyring | MIT | https://github.com/jaraco/keyring |
| platformdirs | MIT | https://github.com/tox-dev/platformdirs |
| tzdata | Apache-2.0 | https://github.com/python/tzdata |
| Anthropic Python SDK (`anthropic`) | Apache-2.0 | https://github.com/anthropics/anthropic-sdk-python. Used only by the optional Claude mode. |
| pydantic, pydantic-core, jiter, annotated-types, typing-inspection, docstring-parser | MIT | Used by the Anthropic SDK |
| sniffio | MIT or Apache-2.0 | Used by the Anthropic SDK |
| opentelemetry-api | Apache-2.0 | Used by the Anthropic SDK |

## Downloaded on first use (not bundled)

| Model | Licence | Notes |
|---|---|---|
| XLM-T sentiment model (`twitter-xlm-roberta-base-sentiment`) | Apache-2.0 | By Cardiff NLP, https://github.com/cardiffnlp/xlm-t. The ONNX export comes from onnx-community on Hugging Face, pinned to one revision and checked with SHA-256. |
| Sentence-embedding model (`paraphrase-multilingual-MiniLM-L12-v2`) | Apache-2.0 | By sentence-transformers (UKP Lab), https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2. The ONNX export comes from onnx-community on Hugging Face, pinned to one revision and checked with SHA-256. |
