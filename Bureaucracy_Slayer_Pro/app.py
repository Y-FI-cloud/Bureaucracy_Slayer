import streamlit as st
import os
import sys
import json
import re
import tempfile
import shutil
import hashlib
import functools
import platform
import atexit
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import logging
import time

import pytesseract
from PIL import Image
from openai import OpenAI, APIError
import docx
import fitz  # PyMuPDF

# ═══════════════════════════════════════════════════════════════
# 🔧 AUTO-DETECT TESSERACT PATH (Cross-platform)
# ═══════════════════════════════════════════════════════════════
def find_tesseract():
    """Αυτόματος εντοπισμός Tesseract σε Windows/Linux/Mac"""
    system = platform.system()
    
    if system == "Windows":
        possible_paths = [
            r'C:\Program Files\Tesseract-OCR\tesseract.exe',
            r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
            r'C:\Users\%USERNAME%\AppData\Local\Tesseract-OCR\tesseract.exe',
        ]
        for path in possible_paths:
            expanded_path = os.path.expandvars(path)
            if os.path.exists(expanded_path):
                return expanded_path
    else:
        # Linux/Mac - ψάξε στο PATH
        import shutil as sh
        tesseract_path = sh.which('tesseract')
        if tesseract_path:
            return tesseract_path
    
    return None

tesseract_path = find_tesseract()
if tesseract_path:
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
else:
    st.warning("⚠️ Tesseract OCR δεν βρέθηκε. Τα scanned PDFs ενδέχεται να μην λειτουργούν.")

# ═══════════════════════════════════════════════════════════════
# 🔤 GREEK FONT DETECTION
# ═══════════════════════════════════════════════════════════════
def get_greek_font_path() -> Optional[str]:
    """Εντοπίζει μια γραμματοσειρά στο σύστημα που υποστηρίζει Ελληνικά."""
    system = platform.system()
    
    if system == "Windows":
        paths = [
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\calibri.ttf",
            r"C:\Windows\Fonts\tahoma.ttf",
            r"C:\Windows\Fonts\times.ttf"
        ]
    elif system == "Darwin":  # macOS
        paths = [
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Times New Roman.ttf"
        ]
    else:  # Linux
        paths = [
            "/usr/share/fonts/truetype/msttcorefonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf"
        ]
        
    for path in paths:
        if os.path.exists(path):
            return path
            
    return None

# ═══════════════════════════════════════════════════════════════
# 📝 LOGGING CONFIGURATION
# ═══════════════════════════════════════════════════════════════
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# ⚙️ CONFIGURATION & PERSISTENCE
# ═══════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Config:
    LM_STUDIO_URL: str = field(default="http://localhost:1234/v1")
    MODEL_NAME: str = field(default="mistral-nemo-instruct")
    OCR_DPI: int = field(default=300)
    MAX_FILE_SIZE_MB: int = field(default=50)
    TEMP_DIR: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "bureaucracy_slayer")
    MAX_TEXT_LENGTH: int = field(default=8000)
    
    # Persistent storage paths
    DATA_DIR: Path = field(default_factory=lambda: Path.home() / ".bureaucracy_slayer")
    PROFILE_FILE: Path = field(default_factory=lambda: Path.home() / ".bureaucracy_slayer" / "user_profile.json")
    
    def __post_init__(self):
        object.__setattr__(self, 'TEMP_DIR', Path(os.getenv("BUREAUCRACY_TEMP_DIR", self.TEMP_DIR)))
        object.__setattr__(self, 'DATA_DIR', Path(os.getenv("BUREAUCRACY_DATA_DIR", self.DATA_DIR)))
        object.__setattr__(self, 'PROFILE_FILE', self.DATA_DIR / "user_profile.json")
        self.TEMP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

@functools.lru_cache(maxsize=1)
def get_config() -> Config:
    return Config()

CONFIG = get_config()

# ═══════════════════════════════════════════════════════════════
# 🧹 TEMP FILE CLEANUP
# ═══════════════════════════════════════════════════════════════
def cleanup_temp_files():
    """Καθαρισμός προσωρινών αρχείων κατά την έξοδο"""
    try:
        temp_dir = CONFIG.TEMP_DIR
        if temp_dir.exists():
            # Διαγραφή μόνο των προσωρινών αρχείων, όχι του ίδιου του φακέλου
            for f in temp_dir.glob("preview_page_*.png"):
                try:
                    f.unlink(missing_ok=True)
                    logger.info(f"Cleaned up: {f}")
                except Exception as e:
                    logger.warning(f"Could not delete {f}: {e}")
            
            # Διαγραφή παλιών filled PDFs (παλαιότερα από 1 ώρα)
            current_time = datetime.now()
            for f in temp_dir.glob("filled_*.pdf"):
                try:
                    file_stat = f.stat()
                    file_age = current_time - datetime.fromtimestamp(file_stat.st_mtime)
                    if file_age > timedelta(hours=1):
                        f.unlink(missing_ok=True)
                        logger.info(f"Cleaned up old PDF: {f}")
                except Exception as e:
                    logger.warning(f"Could not delete {f}: {e}")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

# Καταχώρηση της cleanup function για εκτέλεση κατά την έξοδο
atexit.register(cleanup_temp_files)

# ═══════════════════════════════════════════════════════════════
# 💾 USER PROFILE PERSISTENCE
# ═══════════════════════════════════════════════════════════════
class UserProfileManager:
    """Διαχείριση προφίλ χρήστη με persistent storage"""
    
    @staticmethod
    def load() -> Dict[str, str]:
        """Φόρτωση προφίλ από αρχείο"""
        if CONFIG.PROFILE_FILE.exists():
            try:
                with open(CONFIG.PROFILE_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load profile: {e}")
        return {}
    
    @staticmethod
    def save(profile: Dict[str, str]) -> bool:
        """Αποθήκευση προφίλ σε αρχείο"""
        try:
            with open(CONFIG.PROFILE_FILE, 'w', encoding='utf-8') as f:
                json.dump(profile, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"Failed to save profile: {e}")
            return False

# ═══════════════════════════════════════════════════════════════
# 🎨 STREAMLIT CONFIG & STYLING
# ═══════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Bureaucracy Slayer Pro - AI Agents",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS για modern UI
st.markdown("""
<style>
    /* Main container */
    .main { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
    
    /* Cards */
    .stAlert { border-radius: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
    
    /* Agent visualization */
    .agent-box { 
        background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%); 
        padding: 20px; 
        border-radius: 15px; 
        border-left: 5px solid #1976d2; 
        margin: 15px 0;
        box-shadow: 0 4px 15px rgba(25, 118, 210, 0.2);
        transition: transform 0.3s ease;
    }
    .agent-box:hover { transform: translateY(-2px); }
    
    .agent-active {
        background: linear-gradient(135deg, #c8e6c9 0%, #a5d6a7 100%) !important;
        border-left-color: #4caf50 !important;
        animation: pulse 2s infinite;
    }
    
    @keyframes pulse {
        0% { box-shadow: 0 4px 15px rgba(76, 175, 80, 0.2); }
        50% { box-shadow: 0 4px 25px rgba(76, 175, 80, 0.5); }
        100% { box-shadow: 0 4px 15px rgba(76, 175, 80, 0.2); }
    }
    
    /* Auto-fill results */
    .auto-fill-box { 
        background: linear-gradient(135deg, #fff3e0 0%, #ffe0b2 100%); 
        padding: 20px; 
        border-radius: 15px; 
        border-left: 5px solid #f57c00; 
        margin: 15px 0;
        box-shadow: 0 4px 15px rgba(245, 124, 0, 0.2);
    }
    
    /* Field boxes */
    .field-box { 
        background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%); 
        color: #1e1e1e; 
        padding: 12px 15px; 
        border-radius: 10px; 
        margin: 8px 0; 
        border-left: 4px solid #007bff;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        transition: all 0.3s ease;
    }
    .field-box:hover {
        transform: translateX(5px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    }
    
    /* Success box */
    .success-box { 
        background: linear-gradient(135deg, #d4edda 0%, #c3e6cb 100%); 
        color: #1e1e1e; 
        padding: 15px; 
        border-radius: 10px; 
        border-left: 4px solid #28a745;
        box-shadow: 0 4px 15px rgba(40, 167, 69, 0.2);
    }
    
    /* Progress indicator */
    .progress-step {
        display: flex;
        align-items: center;
        padding: 10px;
        margin: 5px 0;
        border-radius: 8px;
        background: #f5f5f5;
    }
    .progress-step.active { background: #e3f2fd; }
    .progress-step.completed { background: #e8f5e9; }
    
    /* Status badges */
    .status-badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 0.85em;
        font-weight: 600;
    }
    .status-waiting { background: #fff3e0; color: #e65100; }
    .status-working { background: #e3f2fd; color: #1565c0; animation: blink 1s infinite; }
    .status-done { background: #e8f5e9; color: #2e7d32; }
    
    @keyframes blink {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.5; }
    }
    
    /* Communication flow */
    .comm-flow {
        background: #fafafa;
        border: 2px dashed #ddd;
        border-radius: 15px;
        padding: 20px;
        margin: 15px 0;
    }
    
    /* PDF Preview */
    .pdf-preview-container {
        border: 3px solid #1976d2;
        border-radius: 15px;
        overflow: hidden;
        box-shadow: 0 8px 30px rgba(0,0,0,0.2);
    }
    
    /* Page scanning indicator */
    .page-scan-box {
        background: linear-gradient(135deg, #e8f5e9 0%, #c8e6c9 100%);
        border: 2px solid #4caf50;
        border-radius: 12px;
        padding: 15px;
        margin: 10px 0;
        text-align: center;
        animation: scan-pulse 1.5s infinite;
    }
    
    @keyframes scan-pulse {
        0%, 100% { border-color: #4caf50; box-shadow: 0 0 10px rgba(76, 175, 80, 0.3); }
        50% { border-color: #81c784; box-shadow: 0 0 20px rgba(76, 175, 80, 0.6); }
    }
    
    /* Critical info cards */
    .critical-card {
        background: linear-gradient(135deg, #ffebee 0%, #ffcdd2 100%);
        border-left: 5px solid #f44336;
        border-radius: 12px;
        padding: 15px;
        margin: 10px 0;
    }
    
    .warning-card {
        background: linear-gradient(135deg, #fff3e0 0%, #ffe0b2 100%);
        border-left: 5px solid #ff9800;
        border-radius: 12px;
        padding: 15px;
        margin: 10px 0;
    }
    
    .info-card {
        background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
        border-left: 5px solid #2196f3;
        border-radius: 12px;
        padding: 15px;
        margin: 10px 0;
    }
    
    .success-card {
        background: linear-gradient(135deg, #e8f5e9 0%, #c8e6c9 100%);
        border-left: 5px solid #4caf50;
        border-radius: 12px;
        padding: 15px;
        margin: 10px 0;
    }
    
    /* Document summary */
    .doc-summary {
        background: linear-gradient(135deg, #f3e5f5 0%, #e1bee7 100%);
        border-radius: 15px;
        padding: 20px;
        margin: 15px 0;
        box-shadow: 0 4px 15px rgba(156, 39, 176, 0.2);
    }
    
    /* Bullet points styling */
    .bullet-point {
        display: flex;
        align-items: flex-start;
        margin: 8px 0;
        padding: 8px 12px;
        background: rgba(255,255,255,0.7);
        border-radius: 8px;
    }
    .bullet-icon {
        margin-right: 10px;
        font-size: 1.2em;
    }
    
    /* Page thumbnails */
    .page-thumb {
        border: 2px solid #ddd;
        border-radius: 8px;
        padding: 5px;
        margin: 5px;
        text-align: center;
        transition: all 0.3s ease;
    }
    .page-thumb:hover {
        border-color: #1976d2;
        transform: scale(1.05);
    }
    .page-thumb.active {
        border-color: #4caf50;
        background: #e8f5e9;
    }
    .page-thumb.completed {
        border-color: #4caf50;
        opacity: 0.7;
    }
</style>
""", unsafe_allow_html=True)

@functools.lru_cache(maxsize=128)
def compute_file_hash(file_content: bytes) -> str:
    return hashlib.sha256(file_content).hexdigest()[:16]

# ═══════════════════════════════════════════════════════════════
# 🤖 AI CLIENT MANAGER
# ═══════════════════════════════════════════════════════════════
class AIClientManager:
    _instance: Optional[OpenAI] = None
    _last_error: Optional[str] = None
    _connected: bool = False
    
    @classmethod
    def get_client(cls) -> Optional[OpenAI]:
        if cls._instance is None:
            try:
                cls._instance = OpenAI(base_url=CONFIG.LM_STUDIO_URL, api_key="lm-studio", timeout=240.0)
                # Test connection
                cls._instance.models.list()
                cls._connected = True
                logger.info("✅ Connected to LM Studio")
            except Exception as e:
                cls._last_error = str(e)
                cls._connected = False
                logger.warning(f"❌ Could not connect to LM Studio: {e}")
                return None
        return cls._instance
    
    @classmethod
    def is_connected(cls) -> bool:
        if cls._instance is None:
            cls.get_client()
        return cls._connected
    
    @classmethod
    def get_status(cls) -> Tuple[bool, str]:
        if cls.is_connected():
            return True, "🟢 Συνδεδεμένο με LM Studio"
        return False, f"🔴 Αποσυνδεδεμένο: {cls._last_error or 'Άγνωστο σφάλμα'}"

def get_ai_client() -> Optional[OpenAI]:
    return AIClientManager.get_client()

# ═══════════════════════════════════════════════════════════════
# 📱 APP STATE MANAGEMENT
# ═══════════════════════════════════════════════════════════════
class AppState:
    KEYS = {
        'extracted_text': "",
        'analysis_result': None,
        'dynamic_fields': [],
        'tmp_pdf_path': None,
        'file_hash': None,
        'is_pdf': False,
        'form_data': {},
        'filled_pdf_path': None,
        'manual_fields': [],
        'field_positions': {},
        # Agent data - διορθωμένα: αφαίρεση διπλότυπων keys
        'agent1_extracted_data': {},
        'agent2_filled_data': {},
        'auto_filled': False,
        'user_profile': {},
        # UI State
        'agent1_status': 'waiting',
        'agent2_status': 'waiting',
        'processing': False,
        'pdf_preview_pages': [],
        # Document analysis
        'document_summary': None,
        'critical_info': {},
        'scanning_progress': {'current_page': 0, 'total_pages': 0, 'completed_pages': []},
    }
    
    @classmethod
    def init(cls):
        # Load user profile from disk
        if 'user_profile' not in st.session_state:
            st.session_state.user_profile = UserProfileManager.load()
        
        for key, default_value in cls.KEYS.items():
            if key not in st.session_state:
                st.session_state[key] = default_value
    
    @classmethod
    def reset(cls, keep_profile=True):
        profile = st.session_state.get('user_profile', {}) if keep_profile else {}
        for key, default_value in cls.KEYS.items():
            st.session_state[key] = default_value
        if keep_profile:
            st.session_state.user_profile = profile
        # Καθαρισμός και του form_data
        st.session_state.form_data = {}
    
    @classmethod
    def set_agent_status(cls, agent: int, status: str):
        """Set agent status: waiting, working, completed"""
        key = f'agent{agent}_status'
        st.session_state[key] = status
    
    @classmethod
    def update_scanning_progress(cls, current: int, total: int, completed: List[int] = None):
        """Update page scanning progress"""
        st.session_state.scanning_progress = {
            'current_page': current,
            'total_pages': total,
            'completed_pages': completed or []
        }

AppState.init()

# ═══════════════════════════════════════════════════════════════
# 📄 TEXT EXTRACTION WITH PAGE-BY-PAGE PROGRESS
# ═══════════════════════════════════════════════════════════════
def extract_text_from_pdf_with_progress(file_path: str, progress_container) -> Tuple[str, bool, int]:
    """Εξαγωγή κειμένου από PDF με real-time progress per page"""
    doc = None
    try:
        doc = fitz.open(file_path)
        page_count = len(doc)
        if page_count == 0:
            return "", False, 0
        
        # Πρώτα δοκιμάζουμε native extraction
        full_text = []
        needs_ocr = False
        
        for i, page in enumerate(doc):
            text = page.get_text()
            if text.strip():
                full_text.append(f"--- Σελίδα {i + 1} ---\n{text}")
        
        combined_text = "\n\n".join(full_text)
        avg_chars = len(combined_text.strip()) / page_count if page_count > 0 else 0
        
        # Αν έχουμε αρκετό κείμενο, το χρησιμοποιούμε
        if avg_chars > 30:
            return combined_text, False, page_count
        
        # Αλλιώς OCR με page-by-page progress
        needs_ocr = True
        ocr_text = []
        dpi_matrix = fitz.Matrix(CONFIG.OCR_DPI/72, CONFIG.OCR_DPI/72)
        
        # Create progress UI elements
        progress_text = progress_container.empty()
        page_indicators = progress_container.empty()
        progress_bar = progress_container.progress(0)
        
        for page_num, page in enumerate(doc):
            # Update current page
            AppState.update_scanning_progress(page_num + 1, page_count, list(range(page_num)))
            
            # Show current page scanning
            progress_text.markdown(f"""
            <div class="page-scan-box">
                <h3>📄 Σάρωση Σελίδας {page_num + 1} από {page_count}</h3>
                <p>🔍 Εκτέλεση OCR...</p>
            </div>
            """, unsafe_allow_html=True)
            
            # Update page indicators
            indicators_html = "<div style='display: flex; flex-wrap: wrap; justify-content: center;'>"
            for i in range(page_count):
                if i < page_num:
                    status = "completed"
                    icon = "✅"
                elif i == page_num:
                    status = "active"
                    icon = "🔍"
                else:
                    status = ""
                    icon = "⬜"
                indicators_html += f"<div class='page-thumb {status}' style='width: 60px; margin: 5px;'>{icon}<br>Σελ. {i+1}</div>"
            indicators_html += "</div>"
            page_indicators.markdown(indicators_html, unsafe_allow_html=True)
            
            try:
                pix = page.get_pixmap(matrix=dpi_matrix)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                text = pytesseract.image_to_string(img, lang='ell+eng')
                ocr_text.append(f"--- Σελίδα {page_num + 1} ---\n{text}")
                pix = None
            except Exception as e:
                ocr_text.append(f"--- Σελίδα {page_num + 1} ---\n[OCR Error: {e}]")
            
            progress_bar.progress((page_num + 1) / page_count)
        
        # Clear progress UI
        progress_text.empty()
        page_indicators.empty()
        progress_bar.empty()
        
        # Show completion
        progress_container.success(f"✅ Ολοκληρώθηκε η σάρωση {page_count} σελίδων!")
        
        return "\n\n".join(ocr_text), True, page_count
    finally:
        if doc:
            doc.close()

# ═══════════════════════════════════════════════════════════════
# 🤖 AGENT 1: DOCUMENT ANALYZER
# ═══════════════════════════════════════════════════════════════
class DocumentAnalyzer:
    """
    Agent 1: Αναλύει το έγγραφο και εξάγει structured δεδομένα
    """
    
    SYSTEM_PROMPT = """Είσαι ο DocumentAnalyzer Agent. Η δουλειά σου είναι να αναλύεις γραφειοκρατικά έγγραφα και να εξάγεις:

1. **FIELDS**: Λίστα με όλα τα πεδία προς συμπλήρωση που βρίσκεις στο έγγραφο
2. **EXTRACTED_DATA**: Οποιαδήποτε δεδομένα μπορείς να εξάγεις από το κείμενο (ονόματα, διευθύνσεις, ημερομηνίες, ΑΦΜ, κλπ)

Επίστρεψε ΜΟΝΟ JSON με αυτή τη δομή:
{
    "fields": ["Επώνυμο", "Όνομα", "Διεύθυνση", ...],
    "extracted_data": {
        "Επώνυμο": "αν βρεις επώνυμο στο κείμενο",
        "Όνομα": "αν βρεις όνομα",
        "Α.Φ.Μ.": "αν βρεις ΑΦΜ",
        ...
    }
}

Αν δεν βρεις κάποια τιμή, άφησε το κενό.
Βρες ΟΛΑ τα πεδία, ακόμα και αν έχουν σύντομα ονόματα όπως "Τ.Κ", "Α.Φ.Μ.", "Ημερ. Εκδ" """

    SUMMARY_PROMPT = """Είσαι ένας ειδικός στην ανάλυση γραφειοκρατικών εγγράφων. Ανάλυσε το παρακάτω έγγραφο και παράγε:

1. **ΠΕΡΙΛΗΨΗ**: Μια σύντομη περιγραφή του τι είναι το έγγραφο (2-3 προτάσεις)
2. **ΤΥΠΟΣ**: Ο τύπος του εγγράφου (π.χ. κλήση, αίτηση, δήλωση, έγγραφο εφορίας, κλπ)
3. **ΚΡΙΣΙΜΟ**: true/false - Αν το έγγραφο απαιτεί άμεση δράση
4. **ΧΡΗΜΑΤΙΚΟ_ΠΟΣΟ**: Αν υπάρχει πρόστιμο/ποσό προς πληρωμή (ή "Κανένα")
5. **ΠΡΟΘΕΣΜΙΑ**: Ημερομηνία ή χρονικό διάστημα για δράση (ή "Δεν υπάρχει")
6. **ΣΗΜΑΝΤΙΚΑ_ΣΗΜΕΙΑ**: Λίστα με 3-5 bullet points με τα πιο σημαντικά στοιχεία

Επίστρεψε ΜΟΝΟ JSON:
{
    "περιληψη": "...",
    "τυπος": "...",
    "κρισιμο": true/false,
    "χρηματικο_ποσο": "...",
    "προθεσμια": "...",
    "σημαντικα_σημεια": ["...", "...", "..."]
}"""

    @classmethod
    def analyze(cls, text: str) -> Tuple[List[str], Dict[str, str]]:
        """Αναλύει το κείμενο και επιστρέφει (fields, extracted_data)"""
        AppState.set_agent_status(1, 'working')
        client = get_ai_client()
        fields = []
        extracted_data = {}
        
        if client:
            try:
                with st.spinner("🤖 Agent 1 αναλύει το έγγραφο..."):
                    response = client.chat.completions.create(
                        model=CONFIG.MODEL_NAME,
                        messages=[
                            {"role": "system", "content": cls.SYSTEM_PROMPT},
                            {"role": "user", "content": text[:6000]}
                        ],
                        temperature=0.1,
                        max_tokens=1500
                    )
                    content = response.choices[0].message.content
                    fields, extracted_data = cls._parse_response(content)
                    logger.info(f"✅ Agent 1: Βρέθηκαν {len(fields)} πεδία, {len(extracted_data)} δεδομένα")
            except Exception as e:
                logger.warning(f"❌ Agent 1 failed: {e}")
                st.warning(f"⚠️ Agent 1 encountered an issue: {e}")
        else:
            st.info("ℹ️ Λειτουργία χωρίς AI - χρήση regex fallback")
        
        # Fallback: Regex για ελληνικά πεδία
        if not fields:
            fields = cls._fallback_field_extraction(text)
            st.info(f"📋 Regex fallback: Βρέθηκαν {len(fields)} πεδία")
        
        AppState.set_agent_status(1, 'completed')
        return fields, extracted_data
    
    @classmethod
    def generate_summary(cls, text: str) -> Dict[str, Any]:
        """Generate document summary with critical information"""
        client = get_ai_client()
        summary = {
            "περιληψη": "Δεν ήταν δυνατή η ανάλυση του εγγράφου",
            "τυπος": "Άγνωστο",
            "κρισιμο": False,
            "χρηματικο_ποσο": "Άγνωστο",
            "προθεσμια": "Άγνωστο",
            "σημαντικα_σημεια": []
        }
        
        if client:
            try:
                with st.spinner("🤖 Ανάλυση περιεχομένου εγγράφου..."):
                    response = client.chat.completions.create(
                        model=CONFIG.MODEL_NAME,
                        messages=[
                            {"role": "system", "content": cls.SUMMARY_PROMPT},
                            {"role": "user", "content": text[:4000]}
                        ],
                        temperature=0.2,
                        max_tokens=1000
                    )
                    content = response.choices[0].message.content
                    summary = cls._parse_summary(content)
                    logger.info(f"✅ Document summary generated")
            except Exception as e:
                logger.warning(f"❌ Summary generation failed: {e}")
        
        return summary
    
    @staticmethod
    def _parse_summary(content: str) -> Dict[str, Any]:
        """Parse summary JSON response"""
        if not content:
            return {}
        
        cleaned = re.sub(r'```json\s*', '', content)
        cleaned = re.sub(r'```\s*', '', cleaned).strip()
        
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to extract key info manually
            return {
                "περιληψη": "Αδυναμία λεπτομερούς ανάλυσης",
                "τυπος": "Άγνωστο",
                "κρισιμο": False,
                "χρηματικο_ποσο": "Άγνωστο",
                "προθεσμια": "Άγνωστο",
                "σημαντικα_σημεια": []
            }
    
    @staticmethod
    def _parse_response(content: str) -> Tuple[List[str], Dict[str, str]]:
        """Parse του JSON response από τον Agent"""
        if not content:
            return [], {}
        
        # Καθαρισμός από markdown
        cleaned = re.sub(r'```json\s*', '', content)
        cleaned = re.sub(r'```\s*', '', cleaned).strip()
        
        try:
            parsed = json.loads(cleaned)
            fields = parsed.get('fields', [])
            extracted_data = parsed.get('extracted_data', {})
            
            # Καθαρισμός
            fields = [str(f).strip() for f in fields if f]
            extracted_data = {str(k).strip(): str(v).strip() for k, v in extracted_data.items() if v}
            
            return fields, extracted_data
        except json.JSONDecodeError:
            # Fallback: ψάξε για λέξεις σε εισαγωγικά
            matches = re.findall(r'"([^"]+)"', cleaned)
            return [m for m in matches if len(m) > 1], {}
    
    @staticmethod
    def _fallback_field_extraction(text: str) -> List[str]:
        """Fallback με regex αν αποτύχει το AI"""
        pattern = r'([Α-ΩΆΈΉΊΌΎΏα-ωάέήίόύώ\s\.]+?)(?:[…\.:]+|(?:\s*…………))'
        matches = re.findall(pattern, text, re.MULTILINE)
        fields = [m.strip() for m in matches if len(m.strip()) > 2 and len(m.strip()) < 50]
        
        # Deduplication
        seen = set()
        cleaned = []
        for f in fields:
            f = f.strip().rstrip('.').rstrip('…').strip()
            if f and f not in seen and len(f) > 1:
                seen.add(f)
                cleaned.append(f)
        
        return cleaned[:20]

# ═══════════════════════════════════════════════════════════════
# 🤖 AGENT 2: FORM FILLER
# ═══════════════════════════════════════════════════════════════
class FormFiller:
    """
    Agent 2: Παίρνει τα δεδομένα και τα συμπληρώνει στα πεδία
    """
    
    SYSTEM_PROMPT = """Είσαι ο FormFiller Agent. Η δουλειά σου είναι να συμπληρώσεις αυτόματα πεδία φορμών.

Έχεις στη διάθεσή σου:
1. **FIELDS**: Λίστα με πεδία προς συμπλήρωση
2. **EXTRACTED_DATA**: Δεδομένα που βρέθηκαν στο έγγραφο
3. **USER_PROFILE**: Προφίλ χρήστη με προσωπικά στοιχεία

Ταίριαξε κάθε πεδίο με την κατάλληλη τιμή:
- Αν υπάρχει τιμή στο extracted_data, χρησιμοποίησέ την
- Αν όχι, χρησιμοποίησε το user_profile
- Αν δεν βρεις τίποτα, άφησε κενό

Επίστρεψε ΜΟΝΟ JSON:
{
    "filled_data": {
        "Επώνυμο": "Παπαδόπουλος",
        "Όνομα": "Γιάννης",
        ...
    },
    "confidence": "high/medium/low",
    "missing_fields": ["λίστα με κενά πεδία"]
}"""

    @classmethod
    def fill_form(cls, fields: List[str], extracted_data: Dict[str, str], user_profile: Dict[str, str]) -> Dict[str, str]:
        """Συμπληρώνει αυτόματα τα πεδία"""
        AppState.set_agent_status(2, 'working')
        client = get_ai_client()
        filled_data = {}
        
        if client:
            try:
                with st.spinner("🤖 Agent 2 συμπληρώνει τη φόρμα..."):
                    prompt = cls._build_prompt(fields, extracted_data, user_profile)
                    response = client.chat.completions.create(
                        model=CONFIG.MODEL_NAME,
                        messages=[
                            {"role": "system", "content": cls.SYSTEM_PROMPT},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.1,
                        max_tokens=1500
                    )
                    content = response.choices[0].message.content
                    filled_data = cls._parse_response(content, fields)
                    logger.info(f"✅ Agent 2: Συμπληρώθηκαν {len(filled_data)} πεδία")
            except Exception as e:
                logger.warning(f"❌ Agent 2 failed: {e}")
                st.warning(f"⚠️ Agent 2 encountered an issue: {e}")
        
        # Fallback: Απλό matching
        if not filled_data:
            filled_data = cls._fallback_matching(fields, extracted_data, user_profile)
            st.info(f"📋 Fallback matching: Συμπληρώθηκαν {len(filled_data)} πεδία")
        
        AppState.set_agent_status(2, 'completed')
        return filled_data
    
    @staticmethod
    def _build_prompt(fields: List[str], extracted_data: Dict[str, str], user_profile: Dict[str, str]) -> str:
        """Φτιάχνει το prompt για τον Agent"""
        return f"""FIELDS:
{json.dumps(fields, ensure_ascii=False, indent=2)}

EXTRACTED_DATA από το έγγραφο:
{json.dumps(extracted_data, ensure_ascii=False, indent=2)}

USER_PROFILE:
{json.dumps(user_profile, ensure_ascii=False, indent=2)}

Συμπλήρωσε τα πεδία με βάση τα παραπάνω δεδομένα."""
    
    @staticmethod
    def _parse_response(content: str, expected_fields: List[str]) -> Dict[str, str]:
        """Parse του JSON response"""
        if not content:
            return {}
        
        cleaned = re.sub(r'```json\s*', '', content)
        cleaned = re.sub(r'```\s*', '', cleaned).strip()
        
        try:
            parsed = json.loads(cleaned)
            filled_data = parsed.get('filled_data', {})
            return {str(k).strip(): str(v).strip() for k, v in filled_data.items() if v}
        except json.JSONDecodeError:
            return {}
    
    @staticmethod
    def _fallback_matching(fields: List[str], extracted_data: Dict[str, str], user_profile: Dict[str, str]) -> Dict[str, str]:
        """Απλό matching αν αποτύχει το AI"""
        filled_data = {}
        
        for field in fields:
            field_lower = field.lower()
            
            # Ψάξε στο extracted_data
            for key, value in extracted_data.items():
                if field_lower in key.lower() or key.lower() in field_lower:
                    if value:
                        filled_data[field] = value
                        break
            
            # Αν δεν βρέθηκε, ψάξε στο user_profile
            if field not in filled_data:
                for key, value in user_profile.items():
                    if field_lower in key.lower() or key.lower() in field_lower:
                        if value:
                            filled_data[field] = value
                            break
        
        return filled_data

# ═══════════════════════════════════════════════════════════════
# 📄 PDF FILLING & PREVIEW
# ═══════════════════════════════════════════════════════════════

def fill_pdf_intelligently(input_path: str, field_values: Dict[str, str]) -> Tuple[str, int, List[str], Dict]:
    """Συμπληρώνει το PDF με τις τιμές, χρήση Ελληνικής Γραμματοσειράς και εφέ 'Τιπ-Εξ'"""
    doc = None
    filled_count = 0
    errors = []
    filled_details = {}
    
    if not os.path.exists(input_path):
        return "", 0, ["File not found"], {}
    
    output_path = str(CONFIG.TEMP_DIR / f"filled_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
    
    # Αναζήτηση ελληνικής γραμματοσειράς στο σύστημα
    greek_font_path = get_greek_font_path()
    if not greek_font_path:
        logger.warning("Δεν βρέθηκε γραμματοσειρά που να υποστηρίζει Ελληνικά.")
        font_to_use = "helv"  # fallback
    else:
        font_to_use = "grfont"
    
    try:
        doc = fitz.open(input_path)
        
        for page_idx, page in enumerate(doc):
            
            # Ενσωμάτωση της γραμματοσειράς στη σελίδα (αν βρέθηκε)
            if font_to_use == "grfont" and greek_font_path:
                page.insert_font(fontname="grfont", fontfile=greek_font_path)
                
            for field_name, raw_value in field_values.items():
                if not raw_value:
                    continue
                
                # 1. Καθαρισμός των δεδομένων (Sanitization)
                clean_value = re.sub(r'^[\.\[\]\_]+$', '', raw_value.strip())
                clean_value = clean_value.replace('[', '').replace(']', '')
                if not clean_value.strip():
                    continue
                
                result = find_field_with_dots(page, field_name)
                
                if result:
                    label_rect, insert_rect = result
                    try:
                        # 2. Εφέ Τιπ-Εξ: Ζωγραφίζουμε λευκό φόντο για να σβήσουμε τις τελείες του εγγράφου
                        text_length = fitz.get_text_length(clean_value, fontname=font_to_use, fontsize=11)
                        bg_rect = fitz.Rect(
                            insert_rect.x0 - 2, 
                            insert_rect.y1 - 12, 
                            insert_rect.x0 + text_length + 5, 
                            insert_rect.y1 + 2
                        )
                        page.draw_rect(bg_rect, color=(1, 1, 1), fill=(1, 1, 1))
                        
                        # 3. Εισαγωγή του κειμένου
                        page.insert_text(
                            (insert_rect.x0, insert_rect.y1 - 2),
                            clean_value,
                            fontsize=11,
                            color=(0, 0, 0.8), # Σκούρο μπλε
                            fontname=font_to_use 
                        )
                        filled_count += 1
                        filled_details[field_name] = f"Σελίδα {page_idx + 1}, θέση ({insert_rect.x0:.0f}, {insert_rect.y1 - 2:.0f})"
                    except Exception as e:
                        errors.append(f"{field_name}: {e}")
                else:
                    # Partial matching
                    words = field_name.split()
                    for word in words:
                        if len(word) > 3:
                            rects = page.search_for(word)
                            if rects:
                                rect = rects[0]
                                try:
                                    page.insert_text(
                                        (rect.x1 + 15, rect.y1 - 2),
                                        clean_value,
                                        fontsize=11,
                                        color=(0, 0, 0.8),
                                        fontname=font_to_use 
                                    )
                                    filled_count += 1
                                    filled_details[field_name] = f"Σελίδα {page_idx + 1} (partial match)"
                                    break
                                except Exception as e:
                                    errors.append(f"{field_name}: {e}")
                                break
        
        doc.save(output_path, deflate=True, garbage=4)
        return output_path, filled_count, errors, filled_details
        
    except Exception as e:
        logger.error(f"PDF filling failed: {e}")
        return "", 0, [str(e)], {}
    finally:
        if doc:
            doc.close()

def find_field_with_dots(page, field_name: str) -> Optional[Tuple[fitz.Rect, fitz.Rect]]:
    """Βρίσκει το πεδίο και την κατάλληλη θέση εισαγωγής, αποφεύγοντας το γράψιμο πάνω σε άλλο κείμενο."""
    search_patterns = [
        field_name + ":",
        field_name,
        field_name.upper(),
        field_name.title(),
        field_name.replace("Όνομα ", ""),
    ]
    
    for pattern in search_patterns:
        rects = page.search_for(pattern)
        if rects:
            rect = rects[0]
            
            # Ψάχνουμε για τελείες ΚΑΙ υπογραμμίσεις στο ύψος της λέξης
            dot_patterns = ["……", "…", "........", "............", ".....", "_______", "____", "___"]
            
            for dot_pattern in dot_patterns:
                dot_rects = page.search_for(dot_pattern)
                for dot_rect in dot_rects:
                    # ΔΙΟΡΘΩΜΕΝΟ: Αυστηρότερος έλεγχος θέσης - οι τελείες πρέπει να είναι ΔΕΞΙΑ από το label
                    if abs(dot_rect.y0 - rect.y0) < 15 and dot_rect.x0 > rect.x1:
                        insert_rect = fitz.Rect(
                            dot_rect.x0 + 5,
                            rect.y0,
                            dot_rect.x1 + 200,
                            rect.y1
                        )
                        return rect, insert_rect
            
            # Έξυπνο Fallback: Αν δεν βρει γραμμή, ψάχνει για τον πρώτο "κενό χώρο" δεξιά
            # Παίρνουμε όλες τις λέξεις στη σελίδα
            words = page.get_text("words")
            
            # Βρίσκουμε τις λέξεις που είναι στην ίδια γραμμή (περίπου ίδιο y0)
            # και βρίσκονται δεξιά από το rect.x1
            words_on_same_line = [
                w for w in words 
                if abs(w[1] - rect.y0) < 10 and w[0] > rect.x1
            ]
            
            # Ταξινομούμε τις λέξεις με βάση τη συντεταγμένη x (από αριστερά προς τα δεξιά)
            words_on_same_line.sort(key=lambda w: w[0])
            
            insert_x = rect.x1 + 10 # Αρχικό σημείο εκκίνησης
            
            # Αν υπάρχουν άλλες λέξεις στη γραμμή, βρες το πρώτο "μεγάλο" κενό (π.χ. > 20 pixels)
            if words_on_same_line:
                current_x = rect.x1
                for w in words_on_same_line:
                    gap = w[0] - current_x
                    if gap > 30: # Βρήκαμε αρκετό κενό χώρο!
                        insert_x = current_x + 10
                        break
                    current_x = w[2] # Ενημέρωση του current_x στο τέλος της τρέχουσας λέξης
                else:
                    # Αν δεν βρέθηκε μεγάλο κενό ανάμεσα στις λέξεις, πάμε στο τέλος της τελευταίας λέξης
                    insert_x = words_on_same_line[-1][2] + 10
                    
            insert_rect = fitz.Rect(
                insert_x, 
                rect.y0,
                insert_x + 250,
                rect.y1
            )
            return rect, insert_rect
    
    return None

def fill_pdf_intelligently(input_path: str, field_values: Dict[str, str]) -> Tuple[str, int, List[str], Dict]:
    """Συμπληρώνει το PDF με τις τιμές και χρήση Ελληνικής Γραμματοσειράς"""
    doc = None
    filled_count = 0
    errors = []
    filled_details = {}
    
    if not os.path.exists(input_path):
        return "", 0, ["File not found"], {}
    
    output_path = str(CONFIG.TEMP_DIR / f"filled_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
    
    # Αναζήτηση ελληνικής γραμματοσειράς στο σύστημα
    greek_font_path = get_greek_font_path()
    if not greek_font_path:
        logger.warning("Δεν βρέθηκε γραμματοσειρά που να υποστηρίζει Ελληνικά. Ενδέχεται να υπάρξει πρόβλημα κωδικοποίησης.")
        font_to_use = "helv"  # fallback
    else:
        font_to_use = "grfont"
    
    try:
        doc = fitz.open(input_path)
        
        for page_idx, page in enumerate(doc):
            
            # Ενσωμάτωση της γραμματοσειράς στη σελίδα (αν βρέθηκε)
            if font_to_use == "grfont" and greek_font_path:
                page.insert_font(fontname="grfont", fontfile=greek_font_path)
                
            for field_name, value in field_values.items():
                if not value or not value.strip():
                    continue
                
                result = find_field_with_dots(page, field_name)
                
                if result:
                    label_rect, insert_rect = result
                    try:
                        # Χρησιμοποιούμε το insert_rect.y1 - 2 για να κάτσει το κείμενο στο baseline
                        page.insert_text(
                            (insert_rect.x0, insert_rect.y1 - 2),
                            value,
                            fontsize=11,
                            color=(0, 0, 0.8),
                            fontname=font_to_use  # <--- Χρήση της νέας γραμματοσειράς!
                        )
                        filled_count += 1
                        filled_details[field_name] = f"Σελίδα {page_idx + 1}, θέση ({insert_rect.x0:.0f}, {insert_rect.y1 - 2:.0f})"
                    except Exception as e:
                        errors.append(f"{field_name}: {e}")
                else:
                    # Partial matching
                    words = field_name.split()
                    for word in words:
                        if len(word) > 3:
                            rects = page.search_for(word)
                            if rects:
                                rect = rects[0]
                                try:
                                    page.insert_text(
                                        (rect.x1 + 15, rect.y1 - 2),
                                        value,
                                        fontsize=11,
                                        color=(0, 0, 0.8),
                                        fontname=font_to_use  # <--- Χρήση της νέας γραμματοσειράς!
                                    )
                                    filled_count += 1
                                    filled_details[field_name] = f"Σελίδα {page_idx + 1} (partial match)"
                                    break
                                except Exception as e:
                                    errors.append(f"{field_name}: {e}")
                                break
        
        doc.save(output_path, deflate=True, garbage=4)
        return output_path, filled_count, errors, filled_details
        
    except Exception as e:
        logger.error(f"PDF filling failed: {e}")
        return "", 0, [str(e)], {}
    finally:
        if doc:
            doc.close()

def generate_pdf_preview(pdf_path: str, max_pages: int = 3) -> List[str]:
    """Generate PNG previews of PDF pages"""
    previews = []
    doc = None
    
    try:
        doc = fitz.open(pdf_path)
        for page_num in range(min(len(doc), max_pages)):
            page = doc[page_num]
            # Higher resolution for better preview
            mat = fitz.Matrix(2, 2)
            pix = page.get_pixmap(matrix=mat)
            img_path = str(CONFIG.TEMP_DIR / f"preview_page_{page_num}.png")
            pix.save(img_path)
            previews.append(img_path)
    except Exception as e:
        logger.error(f"Preview generation failed: {e}")
    finally:
        if doc:
            doc.close()
    
    return previews

# ═══════════════════════════════════════════════════════════════
# 🎨 UI COMPONENTS
# ═══════════════════════════════════════════════════════════════
def render_agent_status():
    """Εμφάνιση status των agents"""
    st.markdown("### 🤖 Κατάσταση AI Agents")
    
    col1, col2 = st.columns(2)
    
    with col1:
        status1 = st.session_state.get('agent1_status', 'waiting')
        status_class1 = {
            'waiting': 'status-waiting',
            'working': 'status-working', 
            'completed': 'status-done'
        }.get(status1, 'status-waiting')
        
        status_text1 = {
            'waiting': '⏳ Αναμονή',
            'working': '🔄 Επεξεργασία...',
            'completed': '✅ Ολοκληρώθηκε'
        }.get(status1, '⏳ Αναμονή')
        
        st.markdown(f"""
        <div class="agent-box {'agent-active' if status1 == 'working' else ''}">
            <h4>🕵️ Agent 1: DocumentAnalyzer</h4>
            <p>Αναλύει το έγγραφο και εντοπίζει πεδία</p>
            <span class="status-badge {status_class1}">{status_text1}</span>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        status2 = st.session_state.get('agent2_status', 'waiting')
        status_class2 = {
            'waiting': 'status-waiting',
            'working': 'status-working',
            'completed': 'status-done'
        }.get(status2, 'status-waiting')
        
        status_text2 = {
            'waiting': '⏳ Αναμονή',
            'working': '🔄 Επεξεργασία...',
            'completed': '✅ Ολοκληρώθηκε'
        }.get(status2, '⏳ Αναμονή')
        
        st.markdown(f"""
        <div class="agent-box {'agent-active' if status2 == 'working' else ''}">
            <h4>✍️ Agent 2: FormFiller</h4>
            <p>Συμπληρώνει αυτόματα τα πεδία</p>
            <span class="status-badge {status_class2}">{status_text2}</span>
        </div>
        """, unsafe_allow_html=True)

def render_document_summary(summary: Dict[str, Any]):
    """Render document summary with critical information"""
    if not summary:
        return
    
    st.markdown("### 📋 Περιγραφή Εγγράφου")
    
    # Document type and summary
    doc_type = summary.get('τυπος', 'Άγνωστο')
    doc_summary = summary.get('περιληψη', 'Δεν υπάρχει περιγραφή')
    
    st.markdown(f"""
    <div class="doc-summary">
        <h4>📄 {doc_type}</h4>
        <p>{doc_summary}</p>
    </div>
    """, unsafe_allow_html=True)
    
    # Critical information cards
    st.markdown("### ⚡ Σημαντικές Πληροφορίες")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        is_critical = summary.get('κρισιμο', False)
        if is_critical:
            st.markdown("""
            <div class="critical-card">
                <h4>🚨 ΚΡΙΣΙΜΟ</h4>
                <p>Απαιτείται άμεση δράση!</p>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div class="success-card">
                <h4>✅ Μη Κρίσιμο</h4>
                <p>Κανονική προτεραιότητα</p>
            </div>
            """, unsafe_allow_html=True)
    
    with col2:
        amount = summary.get('χρηματικο_ποσο', 'Κανένα')
        if amount and amount != 'Κανένα' and amount != 'Άγνωστο':
            st.markdown(f"""
            <div class="warning-card">
                <h4>💰 Χρηματικό Ποσό</h4>
                <p style="font-size: 1.3em; font-weight: bold;">{amount}</p>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div class="info-card">
                <h4>💰 Χρηματικό Ποσό</h4>
                <p>Δεν απαιτείται πληρωμή</p>
            </div>
            """, unsafe_allow_html=True)
    
    with col3:
        deadline = summary.get('προθεσμια', 'Δεν υπάρχει')
        if deadline and deadline != 'Δεν υπάρχει' and deadline != 'Άγνωστο':
            st.markdown(f"""
            <div class="critical-card">
                <h4>⏰ Προθεσμία</h4>
                <p style="font-size: 1.2em; font-weight: bold;">{deadline}</p>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div class="success-card">
                <h4>⏰ Προθεσμία</h4>
                <p>Δεν υπάρχει χρονικό όριο</p>
            </div>
            """, unsafe_allow_html=True)
    
    # Key points bullets
    key_points = summary.get('σημαντικα_σημεια', [])
    if key_points:
        st.markdown("### 📌 Σημαντικά Σημεία")
        for point in key_points:
            st.markdown(f"""
            <div class="bullet-point">
                <span class="bullet-icon">•</span>
                <span>{point}</span>
            </div>
            """, unsafe_allow_html=True)

def render_user_profile_tab():
    """Tab για ρύθμιση προφίλ χρήστη με persistent storage"""
    st.subheader("👤 Προφίλ Χρήστη για Αυτόματη Συμπλήρωση")
    st.info("📋 Συμπλήρωσε τα στοιχεία σου μία φορά για αυτόματη συμπλήρωση σε όλα τα έγγραφα. Τα δεδομένα αποθηκεύονται τοπικά.")
    
    # Load from session state (which was initialized from disk)
    if 'user_profile' not in st.session_state:
        st.session_state.user_profile = UserProfileManager.load()
    
    profile_categories = {
        "👤 Προσωπικά": ["Επώνυμο", "Όνομα", "Όνομα Πατέρα", "Όνομα Μητέρας", "Ημερομηνία Γέννησης", "Επάγγελμα"],
        "📍 Διεύθυνση": ["Οδός", "Αριθμός", "Πόλη", "Τ.Κ.", "Περιοχή"],
        "🆔 Στοιχεία Ταυτότητας": ["Αριθμός Ταυτότητας", "Α.Φ.Μ.", "Α.Μ.Κ.Α.", "Αρχή Έκδοσης", "Ημερομηνία Έκδοσης"],
        "📞 Επικοινωνία": ["Τηλέφωνο", "Κινητό", "Email"]
    }
    
    tabs = st.tabs(list(profile_categories.keys()))
    
    for tab, (category, fields) in zip(tabs, profile_categories.items()):
        with tab:
            cols = st.columns(2)
            for i, field in enumerate(fields):
                with cols[i % 2]:
                    key = f"profile_{field.replace(' ', '_')}"
                    current_value = st.session_state.user_profile.get(field, "")
                    st.session_state.user_profile[field] = st.text_input(
                        field,
                        value=current_value,
                        key=key
                    )
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("💾 Αποθήκευση Προφίλ", type="primary", use_container_width=True):
            if UserProfileManager.save(st.session_state.user_profile):
                st.success("✅ Το προφίλ αποθηκεύτηκε επιτυχώς!")
                st.balloons()
            else:
                st.error("❌ Σφάλμα κατά την αποθήκευση του προφίλ")
    
    with col2:
        if st.button("🗑️ Καθαρισμός Προφίλ", use_container_width=True):
            st.session_state.user_profile = {}
            UserProfileManager.save({})
            st.warning("🗑️ Το προφίλ διαγράφηκε")
            st.rerun()

def render_auto_fill_results():
    """Εμφανίζει τα αποτελέσματα της αυτόματης συμπλήρωσης"""
    st.markdown("<div class='auto-fill-box'>🤖 <b>Αποτελέσματα Αυτόματης Συμπλήρωσης από τους AI Agents</b></div>", unsafe_allow_html=True)
    
    agent1_data = st.session_state.get('agent1_extracted_data', {})
    agent2_data = st.session_state.get('agent2_filled_data', {})
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**📄 Δεδομένα από το έγγραφο (Agent 1):**")
        if agent1_data:
            for key, value in agent1_data.items():
                st.write(f"• **{key}**: {value}")
        else:
            st.caption("Δεν βρέθηκαν δεδομένα στο έγγραφο")
    
    with col2:
        st.markdown("**✅ Συμπληρωμένα πεδία (Agent 2):**")
        if agent2_data:
            filled_count = len([v for v in agent2_data.values() if v])
            total_count = len(agent2_data)
            # ΔΙΟΡΘΩΜΕΝΟ: Σωστός τύπος δεδομένων για το progress
            progress_value = float(filled_count) / float(total_count) if total_count > 0 else 0.0
            st.progress(progress_value)
            st.caption(f"Συμπληρώθηκαν {filled_count}/{total_count} πεδία")
            
            for key, value in agent2_data.items():
                icon = "✅" if value else "⚪"
                st.write(f"{icon} **{key}**: {value or '(κενό)'}")
        else:
            st.caption("Δεν υπάρχουν συμπληρωμένα πεδία")
    
    # Επιλογή για χειροκίνητη διόρθωση
    st.divider()
    st.subheader("✏️ Διόρθωση Στοιχείων (προαιρετικά)")
    
    fields = st.session_state.get('dynamic_fields', [])
    filled_data = st.session_state.get('agent2_filled_data', {})
    
    if fields:
        cols = st.columns(2)
        for i, field in enumerate(fields):
            with cols[i % 2]:
                current_value = filled_data.get(field, "")
                new_value = st.text_input(
                    f"**{field}**",
                    value=current_value,
                    key=f"edit_{field}_{i}"
                )
                if new_value != current_value:
                    filled_data[field] = new_value
        
        st.session_state.agent2_filled_data = filled_data

def render_pdf_preview(key_suffix: str = "default"):
    """Render PDF preview section"""
    st.subheader("👁️ Προεπισκόπηση Συμπληρωμένου Εγγράφου")
    
    filled_pdf = st.session_state.get('filled_pdf_path')
    
    if filled_pdf and os.path.exists(filled_pdf):
        # Generate previews if not already done
        if not st.session_state.get('pdf_preview_pages'):
            with st.spinner("📄 Δημιουργία προεπισκόπησης..."):
                previews = generate_pdf_preview(filled_pdf)
                st.session_state.pdf_preview_pages = previews
        
        # Display previews
        previews = st.session_state.get('pdf_preview_pages', [])
        if previews:
            st.markdown("<div class='pdf-preview-container'>", unsafe_allow_html=True)
            
            preview_cols = st.columns(min(len(previews), 3))
            for i, (col, preview_path) in enumerate(zip(preview_cols, previews)):
                with col:
                    st.image(preview_path, caption=f"Σελίδα {i+1}", use_container_width=True)
            
            st.markdown("</div>", unsafe_allow_html=True)
        
        # Download button
        with open(filled_pdf, "rb") as f:
            st.download_button(
                "💾 Κατέβασμα Συμπληρωμένου PDF",
                f.read(),
                file_name=f"completed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                mime="application/pdf",
                use_container_width=True,
                type="primary",
                key=f"download_btn_{key_suffix}"
            )
    else:
        st.info("📄 Η προεπισκόπηση θα εμφανιστεί μετά τη συμπλήρωση του PDF")

def render_form_filler_tab():
    """Το κύριο tab για συμπλήρωση φόρμας"""
    if not st.session_state.get('is_pdf'):
        st.info("📄 Μόνο για PDF αρχεία. Ανέβασε ένα PDF για να συμπληρώσεις τη φόρμα.")
        return
    
    fields = list(dict.fromkeys(st.session_state.get('dynamic_fields', [])))
    
    if not fields:
        st.warning("Δεν βρέθηκαν πεδία. Κάνε πρώτα ανάλυση του εγγράφου στην καρτέλα 'Ανάλυση Εγγράφου'.")
        return
    
    st.subheader("🔍 Πεδία που εντοπίστηκαν:")
    
    # Display fields in a nice grid
    cols = st.columns(4)
    for i, field in enumerate(fields):
        with cols[i % 4]:
            st.markdown(f"<div class='field-box'>• {field}</div>", unsafe_allow_html=True)
    
    st.divider()
    
    # Κουμπιά για αυτόματη ή χειροκίνητη συμπλήρωση
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("🤖 Αυτόματη Συμπλήρωση (AI Agents)", type="primary", use_container_width=True):
            with st.spinner("Οι Agents αναλύουν και συμπληρώνουν..."):
                # Reset agent statuses
                AppState.set_agent_status(1, 'waiting')
                AppState.set_agent_status(2, 'waiting')
                
                # Agent 1: Ανάλυση εγγράφου
                text = st.session_state.get('extracted_text', '')
                agent1_fields, agent1_data = DocumentAnalyzer.analyze(text)
                st.session_state.agent1_extracted_data = agent1_data
                
                # Agent 2: Συμπλήρωση φόρμας
                user_profile = st.session_state.get('user_profile', {})
                agent2_data = FormFiller.fill_form(fields, agent1_data, user_profile)
                st.session_state.agent2_filled_data = agent2_data
                st.session_state.auto_filled = True
                st.rerun()
    
    with col2:
        if st.button("✏️ Χειροκίνητη Συμπλήρωση", use_container_width=True):
            st.session_state.auto_filled = False
            # ΔΙΟΡΘΩΜΕΝΟ: Καθαρισμός των filled_data όταν πάμε σε χειροκίνητη λειτουργία
            st.session_state.agent2_filled_data = {}
            st.rerun()
    
    # Εμφάνιση αποτελεσμάτων
    if st.session_state.get('auto_filled'):
        render_auto_fill_results()
    else:
        # Χειροκίνητη συμπλήρωση
        st.subheader("✏️ Χειροκίνητη Συμπλήρωση Πεδίων")
        
        # Κατηγοριοποίηση πεδίων
        personal, location, id_cards, dates, other = [], [], [], [], []
        for f in fields:
            f_lower = f.lower()
            if any(x in f_lower for x in ['ονομα', 'επωνυμο', 'πατερα', 'μητερα', 'επαγγελμα']):
                personal.append(f)
            elif any(x in f_lower for x in ['τοπος', 'διευθυνση', 'τκ', 'ταχυδρομικός', 'οδος', 'περιοχη']):
                location.append(f)
            elif any(x in f_lower for x in ['ταυτότητα', 'αστ', 'αφμ', 'εκδ', 'αρχή', 'μητρωου']):
                id_cards.append(f)
            elif any(x in f_lower for x in ['ημερομηνία', 'έτος', 'ημερ', 'ετος', 'εξαμηνο']):
                dates.append(f)
            else:
                other.append(f)
        
        tabs = st.tabs(["👤 Προσωπικά", "📍 Τοποθεσία", "🆔 Ταυτότητα/ΑΦΜ", "📅 Ημερομηνίες", "📝 Άλλα"])
        all_categories = [personal, location, id_cards, dates, other]
        
        if 'form_data' not in st.session_state:
            st.session_state.form_data = {}
        
        for tab, cat_fields in zip(tabs, all_categories):
            with tab:
                if not cat_fields:
                    st.caption("Δεν υπάρχουν πεδία σε αυτή την κατηγορία")
                    continue
                
                cols = st.columns(2)
                for i, field in enumerate(cat_fields):
                    with cols[i % 2]:
                        # ΔΙΟΡΘΩΜΕΝΟ: Πιο ασφαλής δημιουργία key
                        safe_key = re.sub(r'[^\w]', '_', field)
                        key = f"input_{safe_key}_{i}_{hash(field) % 10000}"  # Προσθήκη hash για μοναδικότητα
                        
                        if key not in st.session_state.form_data:
                            st.session_state.form_data[key] = ""
                        
                        st.session_state.form_data[key] = st.text_input(
                            f"**{field}**",
                            value=st.session_state.form_data[key],
                            key=key
                        )
        
        # Μάζεμα τιμών - ΔΙΟΡΘΩΜΕΝΟ: Χρήση .get() για ασφάλεια
        all_values = {}
        for cat_fields in all_categories:
            for i, field in enumerate(cat_fields):
                safe_key = re.sub(r'[^\w]', '_', field)
                key = f"input_{safe_key}_{i}_{hash(field) % 10000}"
                val = st.session_state.form_data.get(key, "")
                if val and val.strip():
                    all_values[field] = val
        
        st.session_state.agent2_filled_data = all_values
    
    st.divider()
    
    # Κουμπί για τελική συμπλήρωση PDF
    filled_data = st.session_state.get('agent2_filled_data', {})
    
    if filled_data:
        col1, col2 = st.columns([1, 2])
        with col1:
            if st.button("📄 Συμπλήρωση PDF", type="primary", use_container_width=True):
                with st.spinner("Συμπλήρωση PDF σε εξέλιξη..."):
                    tmp_path = st.session_state.get('tmp_pdf_path')
                    output_path, count, errors, details = fill_pdf_intelligently(tmp_path, filled_data)
                
                    if count > 0:
                        st.session_state.filled_pdf_path = output_path
                        st.session_state.pdf_preview_pages = []  # Reset previews
                        st.success(f"✅ Συμπληρώθηκαν {count} πεδία!")
                        
                        with st.expander("🔍 Λεπτομέρειες συμπλήρωσης"):
                            for field, pos in details.items():
                                st.write(f"• **{field}**: {pos}")
                        
                        st.rerun()
                    else:
                        st.error("❌ Δεν συμπληρώθηκε κανένα πεδίο.")
                        if errors:
                            with st.expander("Σφάλματα"):
                                for e in errors:
                                    st.text(e)
        
        with col2:
            filled_count = len([v for v in filled_data.values() if v])
            st.info(f"💡 Έτοιμα για συμπλήρωση: {filled_count}/{len(fields)} πεδία")
    
    # PDF Preview Section
    st.divider()
    render_pdf_preview(key_suffix="filler_tab")

# ═══════════════════════════════════════════════════════════════
# 🚀 MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════
def main():
    # Header
    st.title("🤖 Bureaucracy Slayer Pro")
    st.markdown("<p style='color: #666; font-size: 1.1em;'>Αυτόματη ανάλυση και συμπλήρωση γραφειοκρατικών εγγράφων με 2 AI Agents</p>", unsafe_allow_html=True)
    
    # Sidebar
    with st.sidebar:
        st.markdown("### 🎛️ Ρυθμίσεις")
        
        # AI Status
        connected, status_msg = AIClientManager.get_status()
        if connected:
            st.success(status_msg)
        else:
            st.error(status_msg)
            st.info("💡 Ξεκίνα το LM Studio με το mistral-nemo-instruct model")
        
        st.divider()
        
        # Agent Info
        st.markdown("""
        ### 🤖 AI Agents
        
        **🕵️ Agent 1 - DocumentAnalyzer:**
        • Αναλύει το έγγραφο
        • Εντοπίζει πεδία
        • Εξάγει δεδομένα
        
        **✍️ Agent 2 - FormFiller:**
        • Ταιριάζει δεδομένα
        • Χρησιμοποιεί προφίλ
        • Συμπληρώνει πεδία
        """)
        
        st.divider()
        
        # Profile Summary
        profile = st.session_state.get('user_profile', {})
        filled_fields = len([v for v in profile.values() if v])
        if filled_fields > 0:
            st.success(f"👤 Προφίλ: {filled_fields} πεδία συμπληρωμένα")
        else:
            st.info("👤 Προφίλ: Άδειο")
        
        st.divider()
        
        if st.button("🧹 Καθαρισμός Όλων", use_container_width=True):
            AppState.reset(keep_profile=True)
            st.rerun()
    
    # Agent Status Display
    render_agent_status()
    
    st.divider()
    
    # Main Tabs
    main_tabs = st.tabs(["📄 Ανάλυση Εγγράφου", "👤 Προφίλ Χρήστη", "✍️ Συμπλήρωση", "👁️ Προεπισκόπηση"])
    
    with main_tabs[0]:
        st.subheader("📤 Ανέβασμα Εγγράφου")
        
        uploaded = st.file_uploader(
            "Επίλεξε PDF, DOCX, ή εικόνα",
            type=['pdf', 'docx', 'png', 'jpg', 'jpeg'],
            help="Υποστηρίζονται PDF (native & scanned), Word documents, και εικόνες"
        )
        
        if not uploaded:
            st.info("📂 Ανέβασε ένα έγγραφο για να ξεκινήσει η ανάλυση από τους AI Agents.")
            
            # Show example workflow
            st.markdown("""
            ### 📋 Πώς λειτουργεί:
            1. **Ανέβασε** το έγγραφο (PDF, DOCX, ή εικόνα)
            2. **Agent 1** αναλύει και εντοπίζει τα πεδία
            3. **Agent 2** συμπληρώνει αυτόματα χρησιμοποιώντας το προφίλ σου
            4. **Κατέβασε** το συμπληρωμένο έγγραφο!
            """)
            return
        
        # ΔΙΟΡΘΩΜΕΝΟ: Έλεγχος μεγέθους αρχείου
        file_content = uploaded.getvalue()
        file_size_mb = len(file_content) / (1024 * 1024)
        
        if file_size_mb > CONFIG.MAX_FILE_SIZE_MB:
            st.error(f"❌ Το αρχείο είναι πολύ μεγάλο ({file_size_mb:.1f} MB). Μέγιστο επιτρεπτό: {CONFIG.MAX_FILE_SIZE_MB} MB")
            return
        
        file_hash = compute_file_hash(file_content)
        
        if file_hash != st.session_state.get('file_hash'):
            AppState.reset()
            st.session_state.file_hash = file_hash
            st.session_state.is_pdf = uploaded.type == "application/pdf"
            
            ext = Path(uploaded.name).suffix.lower() or '.pdf'
            tmp_path = CONFIG.TEMP_DIR / f"bs_{file_hash}{ext}"
            with open(tmp_path, "wb") as f:
                f.write(file_content)
            st.session_state.tmp_pdf_path = str(tmp_path)
            st.success(f"✅ Αρχείο αποθηκεύτηκε: {uploaded.name} ({file_size_mb:.1f} MB)")
        
        # Analysis Button
        if st.button("🔍 Εκκίνηση AI Agents - Ανάλυση", type="primary", use_container_width=True):
            # Create a container for scanning progress
            scan_container = st.container()
            
            progress_text = st.empty()
            progress_bar = st.progress(0)
            
            progress_text.text("📖 Εξαγωγή κειμένου από το έγγραφο...")
            progress_bar.progress(25)
            
            text = ""
            try:
                if uploaded.type == "application/pdf":
                    text, _, _ = extract_text_from_pdf_with_progress(st.session_state.tmp_pdf_path, scan_container)
                elif uploaded.type.startswith("image/"):
                    img = Image.open(st.session_state.tmp_pdf_path)
                    text = pytesseract.image_to_string(img, lang='ell+eng')
                else:
                    doc = docx.Document(st.session_state.tmp_pdf_path)
                    text = "\n".join([p.text for p in doc.paragraphs])
            except Exception as e:
                st.error(f"❌ Σφάλμα κατά την εξαγωγή κειμένου: {e}")
                logger.error(f"Text extraction failed: {e}")
                return
            
            # ΔΙΟΡΘΩΜΕΝΟ: Έλεγχος αν βρέθηκε κείμενο
            if not text or not text.strip():
                st.error("❌ Δεν βρέθηκε κείμενο στο έγγραφο. Προσπάθησε με καλύτερη ποιότητα σάρωσης.")
                return
            
            st.session_state.extracted_text = text
            progress_bar.progress(50)
            
            progress_text.text("🤖 Agent 1 αναλύει το έγγραφο...")
            progress_bar.progress(75)
            
            # Agent 1: Ανάλυση εγγράφου
            fields, extracted_data = DocumentAnalyzer.analyze(text)
            st.session_state.dynamic_fields = fields
            st.session_state.agent1_extracted_data = extracted_data
            
            progress_bar.progress(85)
            
            # Generate document summary
            progress_text.text("📋 Δημιουργία περιγραφής εγγράφου...")
            summary = DocumentAnalyzer.generate_summary(text)
            st.session_state.document_summary = summary
            
            progress_bar.progress(100)
            progress_text.empty()
            progress_bar.empty()
            
            st.success(f"✅ Ανάλυση ολοκληρώθηκε! Βρέθηκαν {len(fields)} πεδία, {len(extracted_data)} δεδομένα")
            st.balloons()
        
        # Show document summary if available
        if st.session_state.get('document_summary'):
            render_document_summary(st.session_state.document_summary)
        
        # Show extracted text
        if st.session_state.get('extracted_text'):
            with st.expander("📄 Εξαγόμενο κείμενο (κλικ για προβολή)"):
                st.text_area("Κείμενο:", st.session_state.extracted_text, height=300)
    
    with main_tabs[1]:
        render_user_profile_tab()
    
    with main_tabs[2]:
        if st.session_state.get('dynamic_fields'):
            render_form_filler_tab()
        else:
            st.info("📋 Πήγαινε πρώτα στην καρτέλα 'Ανάλυση Εγγράφου' για να αναλύσεις ένα έγγραφο.")
    
    with main_tabs[3]:
        render_pdf_preview(key_suffix="preview_tab")

if __name__ == "__main__":
    main()