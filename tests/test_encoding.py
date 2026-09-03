"""
tests.test_encoding
===================
Prueba unitaria estricta que audita la codificación UTF-8 sin BOM y la ausencia
total de caracteres corruptos o mojibake en toda la base de código.
"""

import os
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Caracteres típicos de mojibake (interpretación errónea de UTF-8 como GBK o Windows-1252)
MOJIBAKE_SUBSTRINGS = [
    "\xc3\x83", # Doble encoding UTF-8
    "\u8d38", "\u7164", "\u94c6", "\u7322", "\u8305", "\u6973", "\u6451" # Bytes UTF-8 leídos como GBK
]

VALID_EXTENSIONS = [".py", ".md", ".yaml", ".yml", ".json", ".html", ".bat", ".txt"]
EXCLUDED_DIRS = {".git", "__pycache__", "venv", ".pytest_cache", "build", "dist", "data"}
EXCLUDED_FILES = {"test_encoding.py"}


class TestEncodingIntegrity(unittest.TestCase):
    def test_all_files_are_utf8_without_bom_and_no_mojibake(self):
        corrupted_files = []
        bom_files = []

        for root, dirs, files in os.walk(ROOT_DIR):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]

            for fname in files:
                if fname in EXCLUDED_FILES:
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext in VALID_EXTENSIONS:
                    fpath = os.path.join(root, fname)
                    rel_p = os.path.relpath(fpath, ROOT_DIR)

                    with open(fpath, "rb") as fp:
                        raw_bytes = fp.read()

                    # 1. Verificar ausencia de BOM
                    if raw_bytes.startswith(b"\xef\xbb\xbf"):
                        bom_files.append(rel_p)

                    # 2. Verificar decodificación UTF-8 estricta
                    try:
                        text = raw_bytes.decode("utf-8")
                    except UnicodeDecodeError as e:
                        corrupted_files.append(f"{rel_p}: Error decodificando UTF-8 ({e})")
                        continue

                    # 3. Verificar ausencia de mojibake conocido
                    for bad in MOJIBAKE_SUBSTRINGS:
                        if bad in text:
                            corrupted_files.append(f"{rel_p}: Contiene caracter mojibake '{bad}'")
                            break

        self.assertEqual(bom_files, [], f"Archivos con UTF-8 BOM detectados: {bom_files}")
        self.assertEqual(corrupted_files, [], f"Archivos con corrupción o mojibake detectados: {corrupted_files}")


if __name__ == "__main__":
    unittest.main()
