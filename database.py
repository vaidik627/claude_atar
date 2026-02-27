import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'docanalyzer.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            original_name TEXT NOT NULL,
            size INTEGER NOT NULL,
            upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'pending'
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')

    # Insert default settings if they don't exist
    defaults = {
        'dark_mode': 'true',
        'notifications': 'false',
        'auto_analyze': 'true'
    }
    for key, value in defaults.items():
        cursor.execute(
            'INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)',
            (key, value)
        )

    # --- Add OCR columns (safe for existing databases) ---
    ocr_columns = {
        'ocr_status': "TEXT DEFAULT 'pending'",
        'ocr_completed_at': 'TEXT',
        'raw_ocr_path': 'TEXT',
        'processed_text_path': 'TEXT',
        'page_count': 'INTEGER DEFAULT 0',
        'word_count': 'INTEGER DEFAULT 0',
        'ocr_confidence': 'REAL DEFAULT 0.0',
        'error_message': 'TEXT',
    }

    # --- Add extraction columns ---
    extraction_columns = {
        'extraction_status': "TEXT DEFAULT 'pending'",
        'extraction_completed_at': 'TEXT',
        'extraction_path': 'TEXT',
        'extraction_error': 'TEXT',
        'company_name': 'TEXT',
        'fiscal_year_1': 'TEXT',
        'fiscal_year_2': 'TEXT',
        'fiscal_year_3': 'TEXT',
        'ebitda_ltm': 'REAL',
        'revenue_ltm': 'REAL',
        'entry_multiple': 'REAL',
        'purchase_price': 'REAL',
        'confidence_score': 'REAL DEFAULT 0.0',
    }

    # Get existing columns
    cursor.execute('PRAGMA table_info(documents)')
    existing_cols = {row['name'] for row in cursor.fetchall()}

    all_new_columns = {**ocr_columns, **extraction_columns}
    for col_name, col_def in all_new_columns.items():
        if col_name not in existing_cols:
            cursor.execute(
                f'ALTER TABLE documents ADD COLUMN {col_name} {col_def}'
            )

    conn.commit()
    conn.close()


def update_document_ocr_status(doc_id, ocr_status_value, **kwargs):
    """Update OCR fields for a document. Pass any column as a keyword arg."""
    conn = get_db()
    cursor = conn.cursor()

    fields = {'ocr_status': ocr_status_value}
    fields.update(kwargs)

    set_clause = ', '.join(f'{k} = ?' for k in fields)
    values = list(fields.values()) + [doc_id]

    cursor.execute(
        f'UPDATE documents SET {set_clause} WHERE id = ?',
        values
    )

    conn.commit()
    conn.close()


def update_document_extraction(doc_id, **kwargs):
    """Update extraction fields for a document."""
    conn = get_db()
    cursor = conn.cursor()

    set_clause = ', '.join(f'{k} = ?' for k in kwargs)
    values = list(kwargs.values()) + [doc_id]

    cursor.execute(
        f'UPDATE documents SET {set_clause} WHERE id = ?',
        values
    )

    conn.commit()
    conn.close()
