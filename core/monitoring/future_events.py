"""
Future Events Analyzer
Extracts dates from content, analyzes verb tenses, and identifies future-focused content
"""

import re
import logging
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from dateutil import parser

logger = logging.getLogger(__name__)


@dataclass
class FutureEvent:
    """Represents a detected future event"""
    file_id: int
    file_name: str
    file_path: str
    event_date: date
    event_text: str
    context: str
    confidence: float
    event_type: str  # 'explicit_date', 'future_tense', 'temporal_marker'
    detected_at: datetime


class FutureEventsAnalyzer:
    """
    Analyzes content to identify future events and dates.
    
    Features:
    - Extracts all dates from content
    - Identifies future dates (dates > today)
    - Analyzes verb tenses to detect future-focused content
    - Identifies temporal markers (will, going to, scheduled, etc.)
    """
    
    # Future tense indicators
    FUTURE_TENSE_MARKERS = [
        'will', 'shall', 'going to', 'gonna',
        'scheduled', 'planned', 'expected', 'anticipated',
        'upcoming', 'forthcoming', 'future', 'later',
        'soon', 'next', 'coming', 'approaching',
        'deadline', 'due date', 'target date', 'milestone'
    ]
    
    # Temporal relative markers
    TEMPORAL_MARKERS = [
        'tomorrow', 'next week', 'next month', 'next year',
        'in a week', 'in a month', 'in a year',
        'by', 'before', 'after', 'until', 'till'
    ]
    
    # Date patterns
    DATE_PATTERNS = [
        r'\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b',  # YYYY-MM-DD
        r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b',  # DD/MM/YYYY or MM/DD/YYYY
        r'\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})\b',
        r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(\d{4})\b',
    ]
    
    def __init__(self):
        self.today = date.today()
        self.logger = logger
    
    def extract_all_dates(self, content: str) -> List[Tuple[date, str, int]]:
        """
        Extract all dates from content with context.
        
        Returns:
            List of tuples: (date, context_text, position)
        """
        if not content:
            return []
        
        dates_found = []
        
        # Try ISO dates first
        iso_pattern = r'\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b'
        for match in re.finditer(iso_pattern, content):
            try:
                year, month, day = map(int, match.groups())
                if 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
                    parsed_date = date(year, month, day)
                    context = self._extract_context(content, match.start(), match.end(), 100)
                    dates_found.append((parsed_date, context, match.start()))
            except (ValueError, TypeError):
                continue
        
        # Try other date patterns
        for pattern in self.DATE_PATTERNS[1:]:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                try:
                    date_str = match.group(0)
                    parsed_date = parser.parse(date_str, fuzzy=True).date()
                    if 1900 <= parsed_date.year <= 2100:
                        context = self._extract_context(content, match.start(), match.end(), 100)
                        dates_found.append((parsed_date, context, match.start()))
                except (ValueError, TypeError):
                    continue
        
        # Try dateutil parser for fuzzy dates
        try:
            # Look for date-like strings
            fuzzy_pattern = r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b'
            for match in re.finditer(fuzzy_pattern, content):
                try:
                    date_str = match.group(0)
                    parsed_date = parser.parse(date_str, fuzzy=True).date()
                    if 1900 <= parsed_date.year <= 2100:
                        # Check if we already have this date
                        if not any(abs((d[0] - parsed_date).days) < 1 for d in dates_found):
                            context = self._extract_context(content, match.start(), match.end(), 100)
                            dates_found.append((parsed_date, context, match.start()))
                except (ValueError, TypeError):
                    continue
        except Exception as e:
            self.logger.debug(f"Error in fuzzy date parsing: {e}")
        
        return dates_found
    
    def identify_future_dates(self, dates: List[Tuple[date, str, int]]) -> List[Tuple[date, str, int]]:
        """Filter dates to only include future dates"""
        return [(d, ctx, pos) for d, ctx, pos in dates if d > self.today]
    
    def analyze_future_tense(self, content: str) -> List[Dict]:
        """
        Analyze content for future tense indicators.
        
        Returns:
            List of detected future-focused phrases with context
        """
        if not content:
            return []
        
        future_phrases = []
        content_lower = content.lower()
        
        # Check for future tense markers
        for marker in self.FUTURE_TENSE_MARKERS:
            pattern = rf'\b{re.escape(marker)}\b'
            for match in re.finditer(pattern, content_lower):
                context = self._extract_context(content, match.start(), match.end(), 150)
                future_phrases.append({
                    'marker': marker,
                    'context': context,
                    'position': match.start(),
                    'confidence': 0.7 if marker in ['will', 'scheduled', 'planned'] else 0.5
                })
        
        # Check for temporal markers with dates
        for marker in self.TEMPORAL_MARKERS:
            pattern = rf'\b{re.escape(marker)}\b'
            for match in re.finditer(pattern, content_lower):
                # Look for date nearby (within 50 chars)
                nearby_text = content[max(0, match.start()-50):match.end()+50]
                if re.search(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[-/]\d{1,2}[-/]\d{1,2}', nearby_text):
                    context = self._extract_context(content, match.start(), match.end(), 150)
                    future_phrases.append({
                        'marker': marker,
                        'context': context,
                        'position': match.start(),
                        'confidence': 0.8
                    })
        
        return future_phrases
    
    def analyze_content_for_future_events(self, content: str, file_id: int, file_name: str, file_path: str) -> List[FutureEvent]:
        """
        Comprehensive analysis of content for future events.
        
        Analyzes text content to identify dates and events that occur in the future,
        extracting contextual information around those dates.
        
        Args:
            content: Text content to analyze
            file_id: ID of the file being analyzed
            file_name: Name of the file
            file_path: Path to the file
            
        Returns:
            List of FutureEvent objects representing future dates found in the content
        """
        events = []
        
        if not content:
            return events
        
        # 1. Extract all dates
        all_dates = self.extract_all_dates(content)
        
        # 2. Identify future dates
        future_dates = self.identify_future_dates(all_dates)
        
        # 3. Create events for future dates
        for event_date, context, position in future_dates:
            days_until = (event_date - self.today).days
            events.append(FutureEvent(
                file_id=file_id,
                file_name=file_name,
                file_path=file_path,
                event_date=event_date,
                event_text=context[:200],  # First 200 chars of context
                context=context,
                confidence=0.9,  # High confidence for explicit dates
                event_type='explicit_date',
                detected_at=datetime.now()
            ))
        
        # 4. Analyze for future tense indicators
        future_tense_phrases = self.analyze_future_tense(content)
        for phrase in future_tense_phrases:
            # Try to extract date from context
            context = phrase['context']
            dates_in_context = self.extract_all_dates(context)
            future_dates_in_context = self.identify_future_dates(dates_in_context)
            
            if future_dates_in_context:
                # We have a future date in the context
                event_date = future_dates_in_context[0][0]
                events.append(FutureEvent(
                    file_id=file_id,
                    file_name=file_name,
                    file_path=file_path,
                    event_date=event_date,
                    event_text=context[:200],
                    context=context,
                    confidence=phrase['confidence'],
                    event_type='future_tense',
                    detected_at=datetime.now()
                ))
            else:
                # No explicit date, but future tense detected
                # Create event with estimated date (e.g., next week)
                estimated_date = self._estimate_future_date(phrase['marker'])
                if estimated_date:
                    events.append(FutureEvent(
                        file_id=file_id,
                        file_name=file_name,
                        file_path=file_path,
                        event_date=estimated_date,
                        event_text=context[:200],
                        context=context,
                        confidence=phrase['confidence'] * 0.6,  # Lower confidence without explicit date
                        event_type='temporal_marker',
                        detected_at=datetime.now()
                    ))
        
        # Remove duplicates (same file_id, same date, similar context)
        unique_events = []
        seen = set()
        for event in events:
            key = (event.file_id, event.event_date, event.event_type)
            if key not in seen:
                seen.add(key)
                unique_events.append(event)
        
        return unique_events
    
    def _extract_context(self, text: str, start: int, end: int, context_size: int = 100) -> str:
        """Extract context around a match"""
        context_start = max(0, start - context_size)
        context_end = min(len(text), end + context_size)
        return text[context_start:context_end].strip()
    
    def _estimate_future_date(self, marker: str) -> Optional[date]:
        """Estimate future date from temporal marker"""
        marker_lower = marker.lower()
        
        if 'tomorrow' in marker_lower:
            return self.today + timedelta(days=1)
        elif 'next week' in marker_lower or 'in a week' in marker_lower:
            return self.today + timedelta(weeks=1)
        elif 'next month' in marker_lower or 'in a month' in marker_lower:
            return self.today + timedelta(days=30)
        elif 'next year' in marker_lower or 'in a year' in marker_lower:
            return self.today + timedelta(days=365)
        elif 'soon' in marker_lower:
            return self.today + timedelta(days=7)  # Default to a week
        
        return None


def get_future_events_analyzer() -> FutureEventsAnalyzer:
    """Get singleton instance of FutureEventsAnalyzer"""
    if not hasattr(get_future_events_analyzer, '_instance'):
        get_future_events_analyzer._instance = FutureEventsAnalyzer()
    return get_future_events_analyzer._instance

