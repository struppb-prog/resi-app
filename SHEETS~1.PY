"""
sheets_parser.py — Lee y parsea las hojas de Google Sheets de La Playa.

Estructura de la hoja LIMPIEZA:
  Fila 0: Headers de grupos
  Fila 1: Nombres de tareas (col 1, 4, 7, ... cada 3 cols)
  Fila 2: Headers financieros (RESIDENTE, DEBE, meses, TOTAL)
  Fila 3: Primera fila financiera (Diego) + fila de semanas
  Fila 4+: Semanas reales (col 0 = fecha) + datos financieros paralelos
"""

import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, date
import json
import os
import re
import time

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

FREQUENCY_MAP = {
    "S":   "Todos los días (semanal)",
    "M":   "Miércoles",
    "MyJ": "Martes y Jueves",
    "MyV": "Martes y Viernes",
    "V":   "Viernes",
    "J":   "Jueves",
}

MONTHS = ["Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def _freq(task_name: str):
    m = re.search(r"\(([^)]+)\)$", task_name.strip())
    if m:
        code = m.group(1).strip()
        return code, FREQUENCY_MAP.get(code, code)
    return None, "Semanal"


def _clean_name(task_name: str) -> str:
    return re.sub(r"\s*\([^)]+\)\s*$", "", task_name).strip().replace("\n", " ")


def _parse_date(val: str) -> date | None:
    """Intenta parsear una fecha en múltiples formatos."""
    val = str(val).strip()
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


def _to_float(val: str) -> float:
    try:
        return float(str(val).replace("$", "").replace(",", "").replace(".", "").strip() or 0)
    except (ValueError, TypeError):
        return 0.0


class ResidenciaParser:
    CACHE_TTL = 3600  # 1 hora

    def __init__(self, sheet_id: str, creds_path: str):
        self.sheet_id = sheet_id

        # En Railway (o cualquier servidor): leer credenciales desde variable de entorno.
        # En desarrollo local: leer desde el archivo credentials.json.
        creds_json_str = os.environ.get("GOOGLE_CREDS_JSON")
        if creds_json_str:
            creds_dict = json.loads(creds_json_str)
            creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)

        self.client = gspread.authorize(creds)
        self._cache: dict = {}
        self._cache_ts: dict = {}

    # ------------------------------------------------------------------ cache
    def _get_ws_data(self, sheet_name: str) -> list[list[str]]:
        now = time.time()
        if (sheet_name in self._cache
                and now - self._cache_ts.get(sheet_name, 0) < self.CACHE_TTL):
            return self._cache[sheet_name]
        wb = self.client.open_by_key(self.sheet_id)
        data = wb.worksheet(sheet_name).get_all_values()
        self._cache[sheet_name] = data
        self._cache_ts[sheet_name] = now
        return data

    def clear_cache(self):
        self._cache.clear()
        self._cache_ts.clear()

    # -------------------------------------------------------- LIMPIEZA parser
    def _parse_limpieza(self):
        data = self._get_ws_data("LIMPIEZA")

        # --- Task headers (fila 1, una por cada 3 cols empezando en col 1) ---
        task_row = data[1] if len(data) > 1 else []
        tasks = []
        col = 1
        while col < len(task_row) and col < 64:
            raw = task_row[col].strip().replace("\n", " ")
            if raw and raw not in ("G", "I", "Semanas", ""):
                code, freq = _freq(raw)
                tasks.append({
                    "name":  _clean_name(raw),
                    "full":  raw,
                    "freq_code": code,
                    "freq": freq,
                    "col":  col,
                })
            col += 3

        # --- Columna de inicio de sección financiera (busca 'RESIDENTE') ---
        fin_col = 65  # default fallback
        if len(data) > 2:
            header_row = data[2]
            for i, v in enumerate(header_row):
                if str(v).strip() == "RESIDENTE":
                    fin_col = i
                    break

        # --- Parsear semanas y datos financieros en paralelo ---
        weeks = []
        debts = []
        today = date.today()

        for row in data[3:]:
            if not row:
                continue

            # Limpieza: fila de semana real si col 0 parece fecha
            date_obj = _parse_date(row[0]) if row[0] else None
            if date_obj:
                assignments = []
                for task in tasks:
                    c = task["col"]
                    resident = row[c].strip() if c < len(row) else ""
                    g_val    = row[c + 1].strip() if c + 1 < len(row) else ""
                    i_val    = row[c + 2].strip() if c + 2 < len(row) else ""

                    # Ignorar celdas vacías o sin asignar
                    if not resident or resident in ("0", "-", ""):
                        continue

                    assignments.append({
                        "task":      task["name"],
                        "task_full": task["full"],
                        "freq":      task["freq"],
                        "freq_code": task["freq_code"],
                        "resident":  resident,
                        "g":         g_val,
                        "i":         i_val,
                        "done":      (g_val == "X" and i_val == "X"),
                        "partial":   (g_val == "X" or i_val == "X") and not (g_val == "X" and i_val == "X"),
                        "absent":    g_val == "A",
                        "pending":   g_val == "-",
                    })

                weeks.append({
                    "date":     date_obj,
                    "date_str": date_obj.strftime("%d/%m/%Y"),
                    "is_past":  date_obj < today,
                    "assignments": assignments,
                })

            # Finanzas: cualquier fila que tenga nombre en fin_col
            if len(row) > fin_col:
                name = row[fin_col].strip()
                if (name and name not in ("RESIDENTE", "TOTAL ACUMULADO DE DEUDA", "")
                        and not name.startswith("Las multas")):

                    multas = _to_float(row[fin_col + 1]) if len(row) > fin_col + 1 else 0

                    # MONTO FINAL: buscar columna 'TOTAL' o la que tiene el valor grande
                    total = 0
                    total_col = fin_col + 13  # posición típica
                    if len(row) > total_col and row[total_col] not in ("", "-", "TOTAL"):
                        total = _to_float(row[total_col])

                    # Buscar string 'Debe $X'
                    debt_str = ""
                    for v in row[total_col:total_col + 20]:
                        s = str(v).strip()
                        if s.startswith("Debe $"):
                            debt_str = s
                            break
                    if not debt_str and total > 0:
                        debt_str = f"Debe ${int(total):,}".replace(",", ".")

                    # Estado por mes (cols fin_col+15 en adelante = 'Debe', multas, Feb..Dic)
                    monthly = {}
                    months_start = fin_col + 16
                    for i, month in enumerate(MONTHS):
                        ci = months_start + i
                        monthly[month] = row[ci].strip() if ci < len(row) else ""

                    # Evitar duplicados (quedarse con el último si aparece más de una vez)
                    existing = next((d for d in debts if d["name"] == name), None)
                    entry = {
                        "name":      name,
                        "multas":    multas,
                        "total":     total,
                        "debt_str":  debt_str,
                        "monthly":   monthly,
                        "has_debt":  total > 0,
                    }
                    if existing:
                        existing.update(entry)
                    else:
                        debts.append(entry)

        return weeks, tasks, debts

    # --------------------------------------------------------- Public API
    def get_current_week(self) -> dict | None:
        weeks, _, _ = self._parse_limpieza()
        today = date.today()
        current = None
        for w in weeks:
            if w["date"] <= today:
                current = w
            else:
                break
        return current

    def get_next_week(self) -> dict | None:
        weeks, _, _ = self._parse_limpieza()
        today = date.today()
        for w in weeks:
            if w["date"] > today:
                return w
        return None

    def get_current_week_tasks(self) -> list:
        w = self.get_current_week()
        return w["assignments"] if w else []

    def get_current_week_date(self) -> str:
        w = self.get_current_week()
        return w["date_str"] if w else ""

    def get_all_debts(self) -> list:
        _, _, debts = self._parse_limpieza()
        return sorted(debts, key=lambda d: d["total"], reverse=True)

    def get_resident_debt(self, name: str) -> dict | None:
        _, _, debts = self._parse_limpieza()
        name_lower = name.lower()
        return next((d for d in debts if d["name"].lower() == name_lower), None)

    def get_supplies(self) -> list:
        try:
            data = self._get_ws_data("ART. DE LIMPIEZA COMUNES")
        except Exception:
            return []

        supplies = []
        for row in data:
            if not row or not row[0].strip():
                continue
            name = row[0].strip()
            if name in ("Articulos de limpieza", "CONTROL DE ARTICULOS"):
                continue
            current = row[1].strip() if len(row) > 1 else "-"
            to_buy  = row[2].strip() if len(row) > 2 else "0"
            last_date = row[3].strip() if len(row) > 3 else ""
            location  = row[4].strip() if len(row) > 4 else ""

            try:
                needs = float(to_buy) > 0
            except ValueError:
                needs = to_buy not in ("0", "", "-")

            supplies.append({
                "name":      name,
                "current":   current,
                "to_buy":    to_buy,
                "last_date": last_date,
                "location":  location,
                "alert":     needs or current == "-",
            })
        return supplies

    def get_cash_movements(self, limit: int = 30) -> tuple[list, float]:
        try:
            data = self._get_ws_data("Movimientos de caja")
        except Exception:
            return [], 0.0

        movements = []
        balance = 0.0

        for row in data[2:]:
            if not row or not any(row[:5]):
                continue
            income  = row[0].strip() if row[0] else "-"
            expense = row[1].strip() if len(row) > 1 else "-"
            resident = row[2].strip() if len(row) > 2 else ""
            d_str    = row[3].strip() if len(row) > 3 else ""
            detail   = row[4].strip() if len(row) > 4 else ""

            if len(row) > 12 and row[12]:
                try:
                    balance = float(str(row[12]).replace(",", ""))
                except ValueError:
                    pass

            movements.append({
                "income": income, "expense": expense,
                "resident": resident, "date": d_str, "detail": detail,
            })

        return movements[-limit:], balance

    def get_residents_list(self) -> list[str]:
        """Devuelve todos los nombres de residentes que aparecen en la semana actual."""
        tasks = self.get_current_week_tasks()
        seen = set()
        names = []
        for t in tasks:
            n = t["resident"]
            if n and n not in seen:
                seen.add(n)
                names.append(n)
        return sorted(names)

    def get_all_resident_names(self) -> list[str]:
        """Todos los residentes que aparecen en la sección financiera."""
        _, _, debts = self._parse_limpieza()
        return sorted(d["name"] for d in debts)
