"""Google Sheet as the database (same `jobs` and `profile` tabs as the n8n version, plus a tiny `state` tab)."""
import json

from .config import cfg


class Sheet:
    def __init__(self):
        import gspread

        self._gspread = gspread
        gc = gspread.service_account_from_dict(json.loads(cfg.sa_json))
        self.sh = gc.open_by_key(cfg.sheet_id)

    def _ws(self, tab):
        return self.sh.worksheet(tab)

    def read(self, tab):
        vals = self._ws(tab).get_all_values()
        if not vals:
            return []
        header = vals[0]
        out = []
        for i, row in enumerate(vals[1:], start=2):
            d = {h: (row[j] if j < len(row) else "") for j, h in enumerate(header) if h}
            d["_row"] = i
            out.append(d)
        return out

    def append(self, tab, rows):
        if not rows:
            return
        ws = self._ws(tab)
        header = ws.row_values(1)
        ws.append_rows([[("" if r.get(h) is None else r.get(h)) for h in header] for r in rows],
                       value_input_option="RAW")

    def update(self, tab, key_col, key, fields):
        from gspread.utils import rowcol_to_a1

        ws = self._ws(tab)
        header = ws.row_values(1)
        if key_col not in header:
            raise KeyError(f'Column "{key_col}" missing in tab "{tab}"')
        keys = ws.col_values(header.index(key_col) + 1)
        try:
            row = keys.index(str(key)) + 1
        except ValueError:
            raise KeyError(f"Row not found in tab {tab}") from None
        data = []
        for k, v in fields.items():
            if k not in header:
                raise KeyError(f'Column "{k}" missing in tab "{tab}"')
            data.append({"range": rowcol_to_a1(row, header.index(k) + 1), "values": [[v]]})
        ws.batch_update(data, value_input_option="RAW")

    # --- tiny key/value store (replaces n8n's saved state, e.g. the Telegram offset)
    def _state_ws(self):
        try:
            return self.sh.worksheet("state")
        except self._gspread.WorksheetNotFound:
            ws = self.sh.add_worksheet("state", 20, 2)
            ws.append_row(["key", "value"])
            return ws

    def get_state(self, key, default=""):
        for row in self._state_ws().get_all_values()[1:]:
            if row and row[0] == key:
                return row[1] if len(row) > 1 else default
        return default

    def set_state(self, key, value):
        ws = self._state_ws()
        for i, row in enumerate(ws.get_all_values(), start=1):
            if row and row[0] == key:
                ws.update_cell(i, 2, str(value))
                return
        ws.append_row([key, str(value)])
