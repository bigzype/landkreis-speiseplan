#!/usr/bin/env python3
"""Independent artifact checks; no imports from the candidate parser/worktree.

Pure text/ICS checks copied unchanged from the installed live validator.
Invoked by absolute path with Python isolated mode to reject candidate shadowing.
"""
import json
import sys
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from icalendar import Calendar

DAYS = ['Montag','Dienstag','Mittwoch','Donnerstag','Freitag']

def validate_text(data,year,week):
    text=data.decode('utf-8-sig').strip()
    if not re.fullmatch(rf'Speiseplan {year}-KW0?{week}',text.splitlines()[0]):
        raise ValueError('Text heading has wrong ISO week')
    matches=list(re.finditer(r'^(Montag|Dienstag|Mittwoch|Donnerstag|Freitag), (\d{2}\.\d{2}\.\d{4})$',text,re.M))
    if len(matches)!=5: raise ValueError('Text must contain exactly five dated weekdays')
    monday=date.fromisocalendar(year,week,1)
    bodies={}
    for i,match in enumerate(matches):
        day=monday+timedelta(days=i)
        if match[1]!=DAYS[i] or match[2]!=day.strftime('%d.%m.%Y'):
            raise ValueError('Wrong text weekday/date')
        body=text[match.end():matches[i+1].start() if i<4 else len(text)].strip()
        if 'HAUPTGERICHTE' not in body or not any(line.startswith('• ') for line in body.splitlines()):
            raise ValueError('Missing menu contents')
        bodies[day]=body
    return text,bodies


def validate_ics(data,bodies):
    lines=data.splitlines()
    if not lines or lines[0]!=b'BEGIN:VCALENDAR' or lines[-1]!=b'END:VCALENDAR':
        raise ValueError('Invalid VCALENDAR boundaries')
    if any(len(line)>75 for line in lines): raise ValueError('ICS line exceeds 75 UTF-8 bytes')
    cal=Calendar.from_ical(data)
    if str(cal.get('VERSION'))!='2.0': raise ValueError('Invalid iCalendar version')
    if any(getattr(c,'errors',[]) for c in cal.walk()): raise ValueError('iCalendar parser errors')
    events=cal.walk('VEVENT')
    if len(events)!=5: raise ValueError('ICS must contain exactly five events')
    dates=[]; uids=set()
    for event in events:
        start=event.decoded('DTSTART'); end=event.decoded('DTEND')
        if not isinstance(start,datetime) or start.tzinfo is None or not isinstance(end,datetime) or end<=start:
            raise ValueError('Expected valid timed meal events')
        day=start.astimezone(ZoneInfo('Europe/Berlin')).date()
        if day not in bodies: raise ValueError('ICS event outside expected weekdays')
        uid=str(event.get('UID',''))
        if not uid or uid in uids: raise ValueError('Missing or duplicate UID')
        uids.add(uid); dates.append(day)
        description=str(event.get('DESCRIPTION',''))
        # Compare each published text item and category to its corresponding event.
        for line in bodies[day].splitlines():
            if line.startswith('• ') or line in ('EINTOPF','HAUPTGERICHTE','BEILAGEN','GEMÜSEBEILAGEN','DESSERT'):
                if line not in description: raise ValueError(f'Text/ICS menu mismatch on {day}')
    if set(dates)!=set(bodies): raise ValueError('Missing or duplicate event date')
    return {'events':len(events),'dates':[d.isoformat() for d in sorted(dates)]}



if __name__ == "__main__":
    import json,sys
    from datetime import date
    from icalendar import Calendar
    v=json.loads(sys.argv[1]); year,week,_=date.fromisoformat(sys.argv[2]).isocalendar()
    try:
     _,bodies=validate_text(v['text'].encode(),year,week)
     validate_ics(v['ics'].encode(),bodies)
     events=Calendar.from_ical(v['ics'].encode()).walk('VEVENT')
     menus={m['date']:m for m in v['data']['menus']}
     for e in events:
      start=e.decoded('DTSTART'); end=e.decoded('DTEND')
      if start.strftime('%H:%M')!='12:00' or end.strftime('%H:%M')!='13:45' or start.date()!=end.date(): raise ValueError('meal window changed')
      if str(e.get('DTSTART').params.get('TZID'))!='Europe/Berlin' or str(e.get('DTEND').params.get('TZID'))!='Europe/Berlin': raise ValueError('meal timezone changed')
      if str(e.get('LOCATION'))!='Landkreis Restaurant Osnabrück, Am Schölerberg 1, 49082 Osnabrück, Deutschland': raise ValueError('meal location changed')
      if int(e.get('SEQUENCE',-1))<5: raise ValueError('event sequence regressed')
      if str(e.get('TRANSP'))!='OPAQUE': raise ValueError('meal transparency changed')
      if str(e.get('UID'))!='landkreis-speiseplan-'+start.date().isoformat()+'@pro-mac-support.de': raise ValueError('event identity changed')
      if v['data']['source_url']!=str(e.get('URL')): raise ValueError('source URL changed')
      menu=menus[start.date().isoformat()]; description=str(e.get('DESCRIPTION',''))
      for category in ('soups','mains','sides','vegetables','desserts','salads'):
       for item in menu[category]:
        expected=item['text']+(' – '+item['price'] if item.get('price') else '')
        if expected not in description: raise ValueError('normalized item/price missing from ICS')
     print(json.dumps({'ok':True,'week':v['week']}))
    except (ValueError,RuntimeError,TypeError,KeyError,IndexError,AttributeError) as e:
     print(json.dumps({'ok':False,'kind':type(e).__name__,'message':'ICS validation: '+str(e)}))
