# 🤖 Bureaucracy Slayer Pro

An AI-powered application (built with Streamlit) that automates the analysis and form-filling process of Greek bureaucratic documents and forms. 

The app utilizes 2 "AI Agents" (powered by a local LLM for absolute privacy) to extract data from PDFs/Images and automatically fill out forms based on a locally stored user profile.

## 🚀 Features
- **Cross-Platform:** Works seamlessly on Windows, macOS, and Linux.
- **Privacy-First:** Your sensitive data (Tax IDs, ID numbers, addresses) never leaves your machine. The app relies entirely on local models via LM Studio.
- **Hybrid OCR:** Can read native text from PDFs, but also includes a robust fallback to Tesseract OCR for scanned documents (complete with a per-page progress bar).
- **Smart Auto-Fill (Whiteout Effect):** Intelligently detects dotted lines (`......`) in forms, applies a white background (acting like digital whiteout) to hide them, and prints the clean text over it using system fonts that support Greek characters (Arial, Calibri, etc.).

## 🛠️ Prerequisites
1. **Python 3.9+**
2. **Tesseract OCR:** Must be installed on your system (the app automatically detects its installation path).
3. **LM Studio:** Download and open [LM Studio](https://lmstudio.ai/), load an instruction-tuned model (e.g., `mistral-nemo-instruct`), and start the Local Inference Server at `http://localhost:1234/v1`.

## 📦 Installation

1. Clone the repository:
   ```bash
   git clone [https://github.com/Y-FI-cloud/Bureaucracy-Slayer-Pro.git](https://github.com/Y-FI-cloud/Bureaucracy-Slayer-Pro.git)
   cd Bureaucracy-Slayer-Pro
2. Install the required dependencies:

 ```bash

pip install -r requirements.txt
 ```
3. Run the Streamlit application:

 ```bash

streamlit run app.py 
 ```
## 🧠 How it Works: The AI Agents Architecture

The application is not just a simple script; it's a "smart" data processing pipeline that utilizes two distinct AI Agents communicating with a local Large Language Model (LLM).

<img width="1846" height="850" alt="image" src="https://github.com/user-attachments/assets/3881ee46-02bc-4c7b-87fb-7ebca7c52fd7" />

<img width="1823" height="823" alt="image" src="https://github.com/user-attachments/assets/db41aafc-e49a-4925-867a-ae3f61f5c64d" />

### 🕵️‍♂️ Agent 1: DocumentAnalyzer
The first Agent is responsible for "understanding" the document.
1. It receives the raw text of the document (via Native PDF extraction or Tesseract OCR).
2. It analyzes the text (Context Understanding) and extracts **all available fields** that require filling (e.g., "Name", "Tax ID", "Address").
3. It detects any pre-existing data within the document (e.g., a citation number or the date of a traffic violation).
4. **Document Summary:** It generates a smart summary, categorizes the document type (e.g., Traffic Ticket, Solemn Declaration), and flags it if it's critical (looking for deadlines or fines).

### ✍️ Agent 2: FormFiller
The second Agent acts as your digital secretary.
1. It takes the fields discovered by Agent 1 along with your local User Profile.
2. It performs smart matching (Semantic Matching). For example, it understands that the field "Father's Full Name" found in the PDF matches the "Father's Name" saved in your profile.
3. It returns a structured JSON with the exact data ready to be printed on the form.

<img width="444" height="608" alt="image" src="https://github.com/user-attachments/assets/49853c50-a62a-4e00-b6e2-c682be620ea1" />


Where the bue letters and numbers were added by the second agend.
---

## 🛠️ Tech Stack & Tools

To achieve the above workflow with 100% privacy, the application combines some of the best Python libraries:

* **[Streamlit](https://streamlit.io/):** The framework used for the User Interface. It provides instant interactivity, state management (`st.session_state`), and file rendering.
* **[PyMuPDF (fitz)](https://pymupdf.readthedocs.io/):** The "heart" of the PDF processing. It is used for:
  * Extracting native text.
  * Finding the exact coordinates (x, y) of the words in the document.
  * Embedding local system fonts (TTF) for the correct rendering of Greek characters (UTF-8).
  * Creating the "Whiteout Effect" (drawing white rectangles over the document's dotted lines `......` to ensure clean, overlapping-free printing).
* **[Tesseract OCR (pytesseract)](https://github.com/madmaze/pytesseract) & [Pillow (PIL)](https://python-pillow.org/):** Used as a fallback system. If a PDF is scanned (or is a JPG/PNG image), Tesseract takes over the optical character recognition, converting the image into text.
* **[OpenAI Python Client](https://github.com/openai/openai-python):** Used **not** to connect to OpenAI's cloud, but as a standard protocol to connect the app with **[LM Studio](https://lmstudio.ai/)** (Local Inference Server). This allows seamless and zero-cost communication with local open-source models (like `mistral-nemo-instruct`).
* **Regular Expressions (Regex):** Advanced use of the `re` module to sanitize the LLM's "hallucinations" (e.g., removing random symbols like `[.]` or `___` generated by the AI) before writing the final text, and acting as a fallback mechanism for detecting fields if the LLM fails to output valid JSON.
