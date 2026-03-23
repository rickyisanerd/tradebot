from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List

import requests

from .config import Settings


class MacroTrackerError(RuntimeError):
    pass


@dataclass
class MacroEvent:
    event_type: str
    event_date: str
    source: str


class MacroTracker:
    _CPI_URL = "https://www.bls.gov/schedule/news_release/cpi.htm"
    _FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    _DATE_RE = re.compile(r"([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})")

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()

    def refresh(self) -> List[MacroEvent]:
        return self._parse_cpi(self._get_text(self._CPI_URL)) + self._parse_fomc(self._get_text(self._FOMC_URL))

    def _parse_cpi(self, html: str) -> List[MacroEvent]:
        events: List[MacroEvent] = []
        for month, day, year in self._DATE_RE.findall(html):
            event_date = datetime.strptime(f"{month} {day} {year}", "%B %d %Y").date()
            if event_date < datetime.now(timezone.utc).date():
                continue
            events.append(MacroEvent(event_type="cpi", event_date=event_date.isoformat(), source=self._CPI_URL))
        return self._dedupe(events)

    def _parse_fomc(self, html: str) -> List[MacroEvent]:
        events: List[MacroEvent] = []
        for month, day, year in self._DATE_RE.findall(html):
            event_date = datetime.strptime(f"{month} {day} {year}", "%B %d %Y").date()
            if event_date < datetime.now(timezone.utc).date():
                continue
            events.append(MacroEvent(event_type="fomc", event_date=event_date.isoformat(), source=self._FOMC_URL))
        return self._dedupe(events)

    def _dedupe(self, events: List[MacroEvent]) -> List[MacroEvent]:
        seen = set()
        unique: List[MacroEvent] = []
        for event in sorted(events, key=lambda item: (item.event_type, item.event_date)):
            key = (event.event_type, event.event_date)
            if key in seen:
                continue
            seen.add(key)
            unique.append(event)
        return unique

    def _get_text(self, url: str) -> str:
        try:
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
            return response.text
        except Exception as exc:  # noqa: BLE001
            raise MacroTrackerError(f"Unable to fetch macro calendar: {url}") from exc
