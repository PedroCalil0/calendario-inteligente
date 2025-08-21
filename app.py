import os
import sys
import json
import threading
import time
import webbrowser
import datetime as dt
import calendar
import re
import pyperclip

import pygame
import requests
from icalendar import Calendar as ICalendar

APP_TITLE = "Calendário Inteligente (ICS)"
DATA_DIR = os.path.abspath(".")
USER_EVENTS_FILE = os.path.join(DATA_DIR, "events_user.json")
ROUTINES_FILE = os.path.join(DATA_DIR, "routines.json")
ICS_SOURCES_FILE = os.path.join(DATA_DIR, "ics_sources.json")

# Paleta de cores
COLORS = {
    "bg": (20, 22, 28),
    "grid_bg": (30, 34, 44),
    "grid_outline": (60, 65, 78),
    "text": (230, 230, 235),
    "muted_text": (150, 155, 165),
    "today": (50, 110, 220),
    "selected": (90, 95, 115),
    "event_dot_user": (255, 196, 0),
    "event_dot_ics": (80, 200, 120),
    "event_dot_routine": (200, 120, 255),
    "panel_bg": (25, 28, 36),
    "accent": (255, 115, 115),
    "button": (70, 75, 95),
    "input_bg": (40, 44, 54),
}

WEEKDAY_LABELS_PT = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
FULL_WEEKDAY_PT = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
MONTHS_PT = [
    "Janeiro","Fevereiro","Março","Abril","Maio","Junho",
    "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"
]

def safe_load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default

def safe_save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def parse_time_hhmm(s):
    if not s:
        return None
    try:
        parts = s.strip().split(":")
        if len(parts) != 2:
            return None
        h = int(parts[0]); m = int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return (h, m)
    except:
        return None
    return None

def to_date_str(d):
    return d.strftime("%Y-%m-%d")

def to_time_str(hm):
    if hm is None:
        return ""
    return f"{hm[0]:02d}:{hm[1]:02d}"

def from_date_str(s):
    return dt.datetime.strptime(s, "%Y-%m-%d").date()

def clamp(n, lo, hi):
    return max(lo, min(hi, n))

def join_nonempty(*items):
    return " - ".join([x for x in items if x])

def ensure_list(x):
    if isinstance(x, list):
        return x
    return [] if x is None else [x]

def dt_to_local_date_time(dtv):
    # Retorna (date, [hh,mm] ou None)
    # dtv pode ser datetime com tz, datetime sem tz, ou date
    if isinstance(dtv, dt.datetime):
        if dtv.tzinfo is not None:
            local_dt = dtv.astimezone()
        else:
            local_dt = dtv  # assume local
        return local_dt.date(), [local_dt.hour, local_dt.minute]
    elif isinstance(dtv, dt.date):
        return dtv, None
    else:
        return dt.date.today(), None

URL_RE = re.compile(r"https?://\S+")

class ICSClient:
    def __init__(self, sources_file):
        self.sources_file = sources_file
        self.urls = safe_load_json(self.sources_file, [])
        self.events = []
        self.status = "ICS: sem fontes"
        self.lock = threading.Lock()

    def add_source(self, url):
        url = (url or "").strip()
        if not url.lower().startswith("http"):
            return False, "URL inválida"
        self.urls = safe_load_json(self.sources_file, [])
        if url not in self.urls:
            self.urls.append(url)
            safe_save_json(self.sources_file, self.urls)
        return True, "Fonte ICS adicionada"

    def fetch_all(self, timeout=15):
        urls = safe_load_json(self.sources_file, [])
        if not urls:
            with self.lock:
                self.events = []
                self.status = "ICS: sem fontes"
            return
        all_events = []
        total = 0
        errors = 0
        for i, url in enumerate(urls):
            try:
                resp = requests.get(url, timeout=timeout)
                resp.raise_for_status()
                cal = ICalendar.from_ical(resp.text)
                for comp in cal.walk("vevent"):
                    uid = str(comp.get("uid") or f"u{i}-{total}")
                    summary = str(comp.get("summary") or "Evento")
                    dtstart = comp.get("dtstart")
                    if not dtstart:
                        continue
                    d, tm = dt_to_local_date_time(dtstart.dt)
                    # Link (pode estar em URL ou na descrição)
                    link = None
                    urlprop = comp.get("url")
                    if urlprop:
                        link = str(urlprop)
                    else:
                        desc = comp.get("description")
                        if desc:
                            m = URL_RE.search(str(desc))
                            if m:
                                link = m.group(0)

                    all_events.append({
                        "id": f"ics:{i}:{uid}",
                        "title": summary,
                        "date": to_date_str(d),
                        "time": tm,
                        "source": "ics",
                        "meta": {"link": link, "feed": url},
                        "color": "ics",
                    })
                    total += 1
            except Exception:
                errors += 1
                continue
        with self.lock:
            self.events = all_events
            if errors and total:
                self.status = f"ICS: {total} eventos, {errors} falhas"
            elif errors and not total:
                self.status = "ICS: erro ao carregar fontes"
            else:
                self.status = f"ICS: {total} eventos"

    def get_events(self):
        with self.lock:
            return list(self.events)

class Prompt:
    def __init__(self, screen, font, title, initial="", max_len=400, password=False):
        self.screen = screen
        self.font = font
        self.title = title
        self.text = initial
        self.max_len = max_len
        self.password = password
        self.done = False
        self.canceled = False

    def run(self):
        clock = pygame.time.Clock()
        pygame.key.set_repeat(300, 25)
        while not (self.done or self.canceled):
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.canceled = True
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self.canceled = True
                    elif event.key == pygame.K_RETURN:
                        self.done = True
                    elif event.key == pygame.K_BACKSPACE:
                        self.text = self.text[:-1]
                    # SUPORTE AO Ctrl+V
                    elif event.key == pygame.K_v and (event.mod & pygame.KMOD_CTRL):
                        try:
                            clip = pyperclip.paste()
                            if clip:
                                clip = str(clip)
                                remaining = self.max_len - len(self.text)
                                self.text += clip[:remaining]
                        except Exception as e:
                            print("Erro ao colar:", e)
                    else:
                        if event.unicode and len(self.text) < self.max_len:
                            self.text += event.unicode
            self.draw()
            pygame.display.flip()
            clock.tick(60)
        return None if self.canceled else self.text

    def draw(self):
        w, h = self.screen.get_size()
        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        self.screen.blit(overlay, (0, 0))
        box_w, box_h = min(1000, int(w * 0.85)), 260
        rect = pygame.Rect((w - box_w)//2, (h - box_h)//2, box_w, box_h)
        pygame.draw.rect(self.screen, COLORS["input_bg"], rect, border_radius=10)
        pygame.draw.rect(self.screen, COLORS["grid_outline"], rect, width=2, border_radius=10)

        title_surf = self.font.render(self.title, True, COLORS["text"])
        self.screen.blit(title_surf, (rect.x + 20, rect.y + 20))

        shown = self.text if not self.password else ("*" * len(self.text))
        input_surf = self.font.render(shown + " ", True, COLORS["text"])
        ibox = pygame.Rect(rect.x + 20, rect.y + 90, rect.w - 40, 60)
        pygame.draw.rect(self.screen, COLORS["bg"], ibox, border_radius=6)
        pygame.draw.rect(self.screen, COLORS["grid_outline"], ibox, width=1, border_radius=6)
        self.screen.blit(input_surf, (ibox.x + 10, ibox.y + 15))

        hint = "Enter: confirmar | Esc: cancelar | Ctrl+V: colar"
        hint_surf = self.font.render(hint, True, COLORS["muted_text"])
        self.screen.blit(hint_surf, (rect.x + 20, rect.y + rect.h - 40))
class SmartCalendarApp:
    def __init__(self):
        pygame.init()
        info = pygame.display.Info()
        self.screen = pygame.display.set_mode((info.current_w, info.current_h), pygame.FULLSCREEN)
        pygame.display.set_caption(APP_TITLE)
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("Segoe UI", 24)
        self.font_small = pygame.font.SysFont("Segoe UI", 18)
        self.font_large = pygame.font.SysFont("Segoe UI", 36, bold=True)

        today = dt.date.today()
        self.view_year = today.year
        self.view_month = today.month
        self.selected_date = today
        self.fullscreen = True

        # Dados locais
        self.user_events = safe_load_json(USER_EVENTS_FILE, [])
        self.routines = safe_load_json(ROUTINES_FILE, [])
        self.events_for_selected = []
        self.selected_event_index = 0

        # ICS
        self.ics_client = ICSClient(ICS_SOURCES_FILE)
        self.ics_status = "ICS: carregando..."
        self.ics_events_cache = []

        self.ics_stop = False
        self.ics_thread = threading.Thread(target=self.ics_loop, daemon=True)
        self.ics_thread.start()

    def ics_loop(self):
        # Atualiza feeds ICS a cada 15 min
        while not self.ics_stop:
            self.ics_client.fetch_all()
            self.ics_events_cache = self.ics_client.get_events()
            self.ics_status = self.ics_client.status
            for _ in range(15 * 60):
                if self.ics_stop:
                    break
                time.sleep(1)

    def run(self):
        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_LEFT:
                        self.change_month(-1)
                    elif event.key == pygame.K_RIGHT:
                        self.change_month(1)
                    elif event.key == pygame.K_UP:
                        self.change_year(1)
                    elif event.key == pygame.K_DOWN:
                        self.change_year(-1)
                    elif event.key == pygame.K_HOME:
                        self.go_today()
                    elif event.key == pygame.K_f:
                        self.toggle_fullscreen()
                    elif event.key == pygame.K_e:
                        self.add_user_event_wizard()
                    elif event.key == pygame.K_r:
                        self.add_routine_wizard()
                    elif event.key == pygame.K_i:
                        self.add_ics_source_wizard()
                    elif event.key == pygame.K_DELETE:
                        self.delete_selected_if_user_event()
                    elif event.key == pygame.K_RETURN:
                        self.open_selected_link()
                    elif event.key == pygame.K_PAGEUP:
                        self.selected_event_index = max(0, self.selected_event_index - 5)
                    elif event.key == pygame.K_PAGEDOWN:
                        self.selected_event_index = min(max(0, len(self.events_for_selected) - 1),
                                                         self.selected_event_index + 5)

                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 1:
                        self.handle_click(event.pos)
                    elif event.button == 4:
                        self.selected_event_index = max(0, self.selected_event_index - 1)
                    elif event.button == 5:
                        self.selected_event_index = min(max(0, len(self.events_for_selected) - 1),
                                                         self.selected_event_index + 1)

            self.draw()
            pygame.display.flip()
            self.clock.tick(60)

        self.ics_stop = True
        pygame.quit()

    def change_month(self, delta):
        m = self.view_month + delta
        y = self.view_year
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        self.view_month = m
        self.view_year = y

    def change_year(self, delta):
        self.view_year += delta

    def go_today(self):
        today = dt.date.today()
        self.view_year = today.year
        self.view_month = today.month
        self.selected_date = today

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            info = pygame.display.Info()
            self.screen = pygame.display.set_mode((info.current_w, info.current_h), pygame.FULLSCREEN)
        else:
            self.screen = pygame.display.set_mode((1200, 800), pygame.RESIZABLE)

    def handle_click(self, pos):
        w, h = self.screen.get_size()
        cal_rect, panel_rect = self.layout_rects(w, h)
        if cal_rect.collidepoint(pos):
            day = self.day_at_position(pos, cal_rect)
            if day:
                self.selected_date = dt.date(self.view_year, self.view_month, day)
                self.selected_event_index = 0
        elif panel_rect.collidepoint(pos):
            idx = self.event_index_at_position(pos, panel_rect)
            if idx is not None and 0 <= idx < len(self.events_for_selected):
                self.selected_event_index = idx

    def layout_rects(self, w, h):
        panel_w = max(360, int(w * 0.3))
        cal_rect = pygame.Rect(0, 0, w - panel_w, h)
        panel_rect = pygame.Rect(w - panel_w, 0, panel_w, h)
        return cal_rect, panel_rect

    def calendar_grid(self, rect):
        padding = 20
        header_h = 100
        week_h = (rect.h - header_h - padding*2) // 7
        day_h = week_h
        day_w = (rect.w - padding*2) // 7

        cells = []
        head_rect = pygame.Rect(rect.x + padding, rect.y + padding, rect.w - 2*padding, header_h - padding)
        row_y = rect.y + header_h

        cal = calendar.Calendar(firstweekday=0)  # segunda=0
        month_days = list(cal.itermonthdates(self.view_year, self.view_month))
        idx = 0
        for r in range(6):
            for c in range(7):
                x = rect.x + padding + c * day_w
                y = row_y + (r+1) * day_h  # +1 pela linha de labels
                crect = pygame.Rect(x, y, day_w - 6, day_h - 6)
                d = month_days[idx]
                day_val = d.day if d.month == self.view_month else None
                cells.append((crect, (r, c), d if day_val else None))
                idx += 1
        return head_rect, day_w, day_h, cells

    def draw(self):
        self.screen.fill(COLORS["bg"])
        w, h = self.screen.get_size()
        cal_rect, panel_rect = self.layout_rects(w, h)
        self.draw_calendar(cal_rect)
        self.draw_panel(panel_rect)

    def draw_calendar(self, rect):
        pygame.draw.rect(self.screen, COLORS["grid_bg"], rect)
        pygame.draw.rect(self.screen, COLORS["grid_outline"], rect, 2)

        head_rect, day_w, day_h, cells = self.calendar_grid(rect)

        title = f"{MONTHS_PT[self.view_month - 1]} {self.view_year}"
        title_surf = self.font_large.render(title, True, COLORS["text"])
        self.screen.blit(title_surf, (head_rect.x, head_rect.y))

        ics_surf = self.font_small.render(self.ics_status, True, COLORS["muted_text"])
        self.screen.blit(ics_surf, (head_rect.x, head_rect.y + 50))

        for i, lbl in enumerate(WEEKDAY_LABELS_PT):
            lbl_surf = self.font.render(lbl, True, COLORS["muted_text"])
            x = rect.x + 20 + i * ((rect.w - 40) // 7)
            y = rect.y + 100
            self.screen.blit(lbl_surf, (x, y))

        month_events_map = self.events_map_for_month(self.view_year, self.view_month)

        for crect, (r, c), d in cells:
            pygame.draw.rect(self.screen, COLORS["bg"], crect, border_radius=6)
            pygame.draw.rect(self.screen, COLORS["grid_outline"], crect, 1, border_radius=6)
            if d and d.month == self.view_month:
                if d == dt.date.today():
                    pygame.draw.rect(self.screen, COLORS["today"], crect, 3, border_radius=6)
                if d == self.selected_date:
                    pygame.draw.rect(self.screen, COLORS["selected"], crect, 3, border_radius=6)

                day_surf = self.font.render(str(d.day), True, COLORS["text"])
                self.screen.blit(day_surf, (crect.x + 8, crect.y + 6))

                evs = month_events_map.get(to_date_str(d), [])
                if evs:
                    y = crect.y + 40
                    x = crect.x + 12
                    max_show = 6
                    for i, ev in enumerate(evs[:max_show]):
                        color = self.color_for_source(ev.get("source"), ev.get("color"))
                        pygame.draw.circle(self.screen, color, (x, y + i*14), 5)

        self.events_for_selected = self.events_for_date(self.selected_date)
        self.selected_event_index = clamp(self.selected_event_index, 0, max(0, len(self.events_for_selected)-1))

    def draw_panel(self, rect):
        pygame.draw.rect(self.screen, COLORS["panel_bg"], rect)
        pygame.draw.rect(self.screen, COLORS["grid_outline"], rect, 2)

        date_str = self.selected_date.strftime("%d/%m/%Y")
        header = f"{FULL_WEEKDAY_PT[self.selected_date.weekday()].capitalize()}, {date_str}"
        header_surf = self.font_large.render(header, True, COLORS["text"])
        self.screen.blit(header_surf, (rect.x + 16, rect.y + 16))

        hints = "Setas: mês/ano | Home: hoje | E: evento | R: rotina | I: adicionar ICS | Del: excluir | Enter: abrir | F: fullscreen | Esc: sair"
        hints_surf = self.font_small.render(hints, True, COLORS["muted_text"])
        self.screen.blit(hints_surf, (rect.x + 16, rect.y + 64))

        y = rect.y + 100
        list_rect = pygame.Rect(rect.x + 10, y, rect.w - 20, rect.h - y - 10)
        pygame.draw.rect(self.screen, COLORS["bg"], list_rect, border_radius=6)
        inner_pad = 10
        y2 = list_rect.y + inner_pad

        for i, ev in enumerate(self.events_for_selected):
            is_sel = (i == self.selected_event_index)
            row_rect = pygame.Rect(list_rect.x + inner_pad, y2, list_rect.w - 2*inner_pad, 70)
            pygame.draw.rect(self.screen, COLORS["grid_bg"] if not is_sel else COLORS["selected"], row_rect, border_radius=6)

            dot_color = self.color_for_source(ev.get("source"), ev.get("color"))
            pygame.draw.circle(self.screen, dot_color, (row_rect.x + 12, row_rect.y + 18), 7)

            title = ev.get("title", "Evento")
            time_txt = to_time_str(ev.get("time"))
            src = ev.get("source", "user")
            src_label = {"user": "Manual", "ics": "ICS", "routine": "Rotina"}.get(src, src)
            line1 = join_nonempty(title, time_txt)
            l1 = self.font.render(line1, True, COLORS["text"])
            self.screen.blit(l1, (row_rect.x + 30, row_rect.y + 8))

            meta = ev.get("meta", {})
            link = meta.get("link")
            l2_text = src_label + (" (Enter: abrir)" if link else "")
            l2 = self.font_small.render(l2_text, True, COLORS["muted_text"])
            self.screen.blit(l2, (row_rect.x + 30, row_rect.y + 40))

            y2 += row_rect.h + 8
            if y2 > list_rect.bottom - inner_pad:
                break

        legend_y = rect.bottom - 60
        self.draw_legend(rect.x + 16, legend_y)

    def draw_legend(self, x, y):
        entries = [
            ("Manual", COLORS["event_dot_user"]),
            ("ICS", COLORS["event_dot_ics"]),
            ("Rotina", COLORS["event_dot_routine"]),
        ]
        offset_x = 0
        for name, col in entries:
            pygame.draw.circle(self.screen, col, (x + offset_x, y), 7)
            label = self.font_small.render(name, True, COLORS["muted_text"])
            self.screen.blit(label, (x + offset_x + 12, y - 10))
            offset_x += 120

    def color_for_source(self, source, color_key):
        if color_key == "ics":
            return COLORS["event_dot_ics"]
        if color_key == "user":
            return COLORS["event_dot_user"]
        if color_key == "routine":
            return COLORS["event_dot_routine"]
        if source == "ics":
            return COLORS["event_dot_ics"]
        if source == "routine":
            return COLORS["event_dot_routine"]
        return COLORS["event_dot_user"]

    def events_map_for_month(self, year, month):
        events = []
        for e in self.user_events:
            try:
                d = from_date_str(e["date"])
                if d.year == year and d.month == month:
                    events.append(e)
            except Exception:
                pass
        for e in self.ics_events_cache:
            try:
                d = from_date_str(e["date"])
                if d.year == year and d.month == month:
                    events.append(e)
            except Exception:
                pass
        events += self.expand_routines_for_month(year, month)

        m = {}
        for e in events:
            ds = e.get("date")
            if not ds:
                continue
            m.setdefault(ds, []).append(e)

        for ds, lst in m.items():
            lst.sort(key=lambda ev: (999 if ev.get("time") is None else ev["time"][0]*60 + ev["time"][1], ev.get("title","")))
        return m

    def events_for_date(self, d):
        ds = to_date_str(d)
        m = self.events_map_for_month(d.year, d.month)
        return m.get(ds, [])

    def day_at_position(self, pos, rect):
        head_rect, day_w, day_h, cells = self.calendar_grid(rect)
        for crect, (r, c), d in cells:
            if crect.collidepoint(pos) and d and d.month == self.view_month:
                return d.day
        return None

    def event_index_at_position(self, pos, panel_rect):
        y = panel_rect.y + 100
        list_rect = pygame.Rect(panel_rect.x + 10, y, panel_rect.w - 20, panel_rect.h - y - 10)
        inner_pad = 10
        y2 = list_rect.y + inner_pad
        for i, ev in enumerate(self.events_for_selected):
            row_rect = pygame.Rect(list_rect.x + inner_pad, y2, list_rect.w - 2*inner_pad, 70)
            if row_rect.collidepoint(pos):
                return i
            y2 += row_rect.h + 8
            if y2 > list_rect.bottom - inner_pad:
                break
        return None

    def add_user_event_wizard(self):
        title = self.prompt("Título do evento:", "")
        if title is None or not title.strip():
            return
        default_date = to_date_str(self.selected_date)
        date_str = self.prompt(f"Data (YYYY-MM-DD):", default_date)
        if date_str is None:
            return
        try:
            d = from_date_str(date_str)
        except:
            self.info("Data inválida.")
            return
        time_str = self.prompt("Hora (HH:MM) opcional, Enter para pular:", "")
        if time_str is None:
            return
        tm = parse_time_hhmm(time_str) if time_str.strip() else None

        eid = f"user:{int(time.time()*1000)}"
        ev = {
            "id": eid,
            "title": title.strip(),
            "date": to_date_str(d),
            "time": [tm[0], tm[1]] if tm else None,
            "source": "user",
            "meta": {},
            "color": "user",
        }
        self.user_events.append(ev)
        safe_save_json(USER_EVENTS_FILE, self.user_events)

    def add_routine_wizard(self):
        title = self.prompt("Título da rotina:", "")
        if title is None or not title.strip():
            return
        days_str = self.prompt("Dias da semana (ex: seg,ter,qua | 'todos'):", "todos")
        if days_str is None:
            return
        days_idx = self.parse_weekdays(days_str)
        if days_idx is None or not days_idx:
            self.info("Dias inválidos.")
            return
        time_str = self.prompt("Hora (HH:MM):", "07:00")
        tm = parse_time_hhmm(time_str or "")
        if not tm:
            self.info("Hora inválida.")
            return
        start_str = self.prompt("Data inicial (YYYY-MM-DD):", to_date_str(self.selected_date))
        if start_str is None:
            return
        try:
            start_date = from_date_str(start_str)
        except:
            self.info("Data inicial inválida.")
            return
        end_str = self.prompt("Data final (YYYY-MM-DD) opcional, Enter para pular:", "")
        end_date = None
        if end_str and end_str.strip():
            try:
                end_date = from_date_str(end_str.strip())
            except:
                self.info("Data final inválida.")
                return

        rid = f"routine:{int(time.time()*1000)}"
        routine = {
            "id": rid,
            "title": title.strip(),
            "type": "weekly",
            "days_of_week": days_idx,  # 0=Seg ... 6=Dom
            "time": [tm[0], tm[1]],
            "start_date": to_date_str(start_date),
            "end_date": to_date_str(end_date) if end_date else None,
        }
        self.routines.append(routine)
        safe_save_json(ROUTINES_FILE, self.routines)

    def add_ics_source_wizard(self):
        url = self.prompt("Cole a URL ICS (iCal) do calendário (ex: Google Agenda > Endereço secreto iCal):", "")
        if url is None:
            return
        ok, msg = self.ics_client.add_source(url)
        self.info(msg)
        # Força atualização imediata
        self.ics_client.fetch_all()
        self.ics_events_cache = self.ics_client.get_events()
        self.ics_status = self.ics_client.status

    def delete_selected_if_user_event(self):
        if not self.events_for_selected:
            return
        ev = self.events_for_selected[self.selected_event_index]
        if ev.get("source") != "user":
            self.info("Apenas eventos manuais podem ser excluídos.")
            return
        eid = ev.get("id")
        self.user_events = [e for e in self.user_events if e.get("id") != eid]
        safe_save_json(USER_EVENTS_FILE, self.user_events)
        self.info("Evento excluído.")

    def open_selected_link(self):
        if not self.events_for_selected:
            return
        ev = self.events_for_selected[self.selected_event_index]
        link = (ev.get("meta") or {}).get("link")
        if link:
            try:
                webbrowser.open(link)
            except:
                pass

    def parse_weekdays(self, s):
        s = (s or "").strip().lower()
        if s == "todos":
            return [0,1,2,3,4,5,6]
        parts = [p.strip() for p in s.split(",") if p.strip()]
        mapping = {
            "seg":0,"segunda":0,
            "ter":1,"terça":1,"terca":1,
            "qua":2,"quarta":2,
            "qui":3,"quinta":3,
            "sex":4,"sexta":4,
            "sab":5,"sábado":5,"sabado":5,
            "dom":6,"domingo":6
        }
        out = []
        for p in parts:
            idx = mapping.get(p)
            if idx is None:
                return None
            if idx not in out:
                out.append(idx)
        return sorted(out)

    def expand_routines_for_month(self, year, month):
        results = []
        first = dt.date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        last = dt.date(year, month, last_day)
        for r in self.routines:
            if r.get("type") != "weekly":
                continue
            days = r.get("days_of_week", [])
            tm = r.get("time")
            try:
                start = from_date_str(r.get("start_date"))
            except:
                continue
            end = r.get("end_date")
            end = from_date_str(end) if end else None

            cur = first
            while cur <= last:
                if cur.weekday() in days:
                    if cur >= start and (end is None or cur <= end):
                        results.append({
                            "id": f"routine:{r.get('id')}:{to_date_str(cur)}",
                            "title": r.get("title", "Rotina"),
                            "date": to_date_str(cur),
                            "time": tm,
                            "source": "routine",
                            "meta": {},
                            "color": "routine",
                        })
                cur += dt.timedelta(days=1)
        return results

    def prompt(self, title, initial=""):
        p = Prompt(self.screen, self.font, title, initial)
        return p.run()

    def info(self, message):
        p = Prompt(self.screen, self.font, message, "")
        p.run()

def main():
    app = SmartCalendarApp()
    app.run()

if __name__ == "__main__":
    main()