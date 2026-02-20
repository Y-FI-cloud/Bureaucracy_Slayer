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
   git clone [https://github.com/Y-FI-cloud/Bureaucracy-Slayer-Pro.git](https://github.com/YOUR_USERNAME/Bureaucracy-Slayer-Pro.git)
   cd Bureaucracy-Slayer-Pro
2. Install the required dependencies:

 ```bash

pip install -r requirements.txt
 ```
3. Run the Streamlit application:

 ```bash

streamlit run app.py
