import os, json, re, math, requests
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
import streamlit as st
import plotly.express as px
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="FESTPILOT | Event Intelligence", page_icon="✦", layout="wide", initial_sidebar_state="expanded")
BASE = Path(__file__).resolve().parent
DEFAULT_DATA_PATH = BASE / 'data' / 'FestPilot_Bengaluru_Dataset.xlsx'
DATA_PATH = os.environ.get('FESTPILOT_DATA', str(DEFAULT_DATA_PATH))
GEOAPIFY_API_KEY = os.environ.get('GEOAPIFY_API_KEY','')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY','')

# Bengaluru-only search boundary: 35 km from central Bengaluru, with an additional coordinate check.
BLR_CENTER = (12.9716, 77.5946)
BLR_RADIUS_KM = 35.0
BLR_RECT = {"low": {"latitude": 12.65, "longitude": 77.25}, "high": {"latitude": 13.28, "longitude": 77.95}}

@st.cache_data(ttl=300, show_spinner=False)
def read_sheet(name):
    try: return pd.read_excel(DATA_PATH, sheet_name=name)
    except Exception: return pd.DataFrame()
venues = read_sheet('Venues'); events = read_sheet('Events'); resources = read_sheet('Resources'); budget = read_sheet('Budget'); guidelines = read_sheet('Guidelines')

def colval(row, key, default=''):
    try: return row[key] if key in row and pd.notna(row[key]) else default
    except Exception: return default

def split_items(value): return [x.strip().lower() for x in str(value or '').split(';') if x.strip()]
def money(x): return f"₹{float(x or 0):,.0f}"
def haversine_km(lat, lon):
    p1,p2=math.radians(BLR_CENTER[0]),math.radians(float(lat)); dl=math.radians(float(lon)-BLR_CENTER[1]); dp=p2-p1
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 6371*2*math.asin(math.sqrt(a))

def geoapify_venue_search(query, max_results=20):
    """Live Geoapify Places API v2 search, constrained to a 35 km Bengaluru circle."""
    if not GEOAPIFY_API_KEY:
        return pd.DataFrame(), 'Add your Geoapify API key to your environment or Streamlit secrets.'
    q = (query or 'event venue').strip().lower()

    # Geoapify uses hierarchical place categories rather than free-form venue-type search.
    # Keep categories focused on places likely to host events; the user's phrase is also
    # used as a name filter only when it looks like a specific venue name.
    if any(k in q for k in ['stadium', 'sports', 'arena', 'ground']):
        categories = 'sport,activity.events_venue'
    elif any(k in q for k in ['auditorium', 'convention', 'banquet', 'conference', 'event hall', 'function hall']):
        categories = 'activity.events_venue,entertainment.culture'
    elif any(k in q for k in ['outdoor', 'lawn', 'garden', 'park', 'open ground']):
        categories = 'leisure,activity.events_venue,sport'
    else:
        categories = 'activity.events_venue,entertainment.culture,leisure,sport'

    params = {
        'categories': categories,
        'filter': f'circle:{BLR_CENTER[1]},{BLR_CENTER[0]},{int(BLR_RADIUS_KM * 1000)}',
        'bias': f'proximity:{BLR_CENTER[1]},{BLR_CENTER[0]}',
        'limit': min(max(int(max_results), 1), 100),
        'lang': 'en',
        'apiKey': GEOAPIFY_API_KEY
    }
    try:
        resp = requests.get('https://api.geoapify.com/v2/places', params=params, timeout=25)
        if resp.status_code != 200:
            return pd.DataFrame(), f'Geoapify Places API error {resp.status_code}: {resp.text[:500]}'
        features = resp.json().get('features', [])
        rows = []
        for feature in features:
            p = feature.get('properties') or {}
            lat, lon = p.get('lat'), p.get('lon')
            if lat is None or lon is None:
                continue
            distance = haversine_km(lat, lon)
            # Second independent distance check ensures no result escapes the intended scope.
            if distance > BLR_RADIUS_KM:
                continue
            raw = ((p.get('datasource') or {}).get('raw') or {})
            contact = p.get('contact') or {}
            phone = p.get('phone') or contact.get('phone') or raw.get('phone') or raw.get('contact:phone') or ''
            website = p.get('website') or contact.get('website') or raw.get('website') or raw.get('contact:website') or ''
            place_id = p.get('place_id', '')
            map_url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=17/{lat}/{lon}"
            cats = p.get('categories') or []
            rows.append({
                'Place ID': place_id,
                'Venue': p.get('name') or p.get('address_line1') or 'Unnamed venue',
                'Address': p.get('formatted') or ', '.join(x for x in [p.get('address_line1'), p.get('address_line2')] if x),
                'Type': ', '.join(cats[:3]) if cats else 'Event / activity venue',
                'Rating': None,
                'Reviews': None,
                'Phone': phone,
                'Website': website,
                'Map': map_url,
                'Status': 'Listing status not supplied',
                'Latitude': float(lat),
                'Longitude': float(lon),
                'Distance from central Bengaluru (km)': round(distance, 1),
                'Source': 'Geoapify Places API (live search)'
            })
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.drop_duplicates(subset=['Place ID'] if 'Place ID' in df else ['Venue','Address'])
            df = df.sort_values('Distance from central Bengaluru (km)', ascending=True)
            df = df.head(max_results)
        return df, '' if not df.empty else 'No matching Geoapify listings found inside the Bengaluru search boundary. Try another venue category.'
    except Exception as e:
        return pd.DataFrame(), f'Could not reach Geoapify Places API: {e}'

def venue_search_local(city='', venue_type='Any', min_capacity=0, max_budget=1000000, required=''):
    if venues.empty: return pd.DataFrame()
    req=split_items(required); out=[]
    for _,v in venues.iterrows():
        cap=float(colval(v,'capacity',0) or 0); rent=float(colval(v,'estimated_rent_inr',0) or 0)
        if cap<min_capacity or rent>max_budget: continue
        if city and city.lower() not in str(colval(v,'city','')).lower(): continue
        if venue_type!='Any' and str(colval(v,'venue_type','')).lower()!=venue_type.lower(): continue
        fac=split_items(colval(v,'facilities','')); missing=[r for r in req if not any(r in f or f in r for f in fac)]
        score=round(100*(.55*(1 if not req else (len(req)-len(missing))/len(req))+.25*max(0,1-rent/max(max_budget,1))+.20*(min(cap/max(min_capacity,1),2)/2 if min_capacity else .7)),1)
        out.append({'Venue':colval(v,'venue_name'),'Area':colval(v,'area'),'City':colval(v,'city'),'Type':colval(v,'venue_type'),'Capacity':int(cap),'Estimated rent (₹)':int(rent),'Match':score,'Facilities':colval(v,'facilities'),'Missing facilities':', '.join(missing) or 'None','Venue ID':colval(v,'venue_id'),'Source':'Uploaded workbook'})
    return pd.DataFrame(out).sort_values('Match',ascending=False) if out else pd.DataFrame()

def weather_forecast(lat,lon,event_day,start='15:00',end='20:00'):
    target=pd.to_datetime(event_day).date()
    if target<date.today() or target>date.today()+timedelta(days=15): return {'status':'Forecast window unavailable','message':'Check again closer to the event date.'},pd.DataFrame()
    try:
        params={'latitude':float(lat),'longitude':float(lon),'hourly':'temperature_2m,precipitation_probability,precipitation,wind_speed_10m,wind_gusts_10m','timezone':'auto','forecast_days':16}
        data=requests.get('https://api.open-meteo.com/v1/forecast',params=params,timeout=18).json(); h=pd.DataFrame(data.get('hourly',{}))
        if h.empty:return {'status':'No forecast returned'},h
        h['time']=pd.to_datetime(h['time']); lo=pd.to_datetime(f'{target} {start}'); hi=pd.to_datetime(f'{target} {end}'); win=h[(h.time>=lo)&(h.time<=hi)]
        if win.empty:return {'status':'No hourly forecast for this window'},win
        rain=float(win.precipitation_probability.max()); mm=float(win.precipitation.sum()); gust=float(win.wind_gusts_10m.max()); wind=float(win.wind_speed_10m.max()); temp=float(win.temperature_2m.max())
        risk='Low'
        if rain>=70 or mm>=10 or gust>=50:risk='High'
        elif rain>=40 or mm>=3 or gust>=35:risk='Moderate'
        rec=[]
        if rain>=40 or mm>=3:rec+=['Confirm covered/indoor backup','Weatherproof electrical equipment','Plan safe movement on wet surfaces']
        if gust>=35:rec+=['Have qualified personnel inspect temporary stages, trusses and tents']
        if temp>=34:rec+=['Provide water, shade and rest areas']
        if not rec:rec=['Continue monitoring; forecasts can change']
        return {'status':risk+' planning risk','rain_probability':round(rain,1),'rainfall_mm':round(mm,1),'max_wind_kmh':round(wind,1),'max_gust_kmh':round(gust,1),'max_temperature_c':round(temp,1),'recommendations':rec},win
    except Exception as e:return {'status':'Weather service unavailable','message':str(e),'recommendations':['Check official IMD forecasts and warnings']},pd.DataFrame()

def checklist(name,req=''):
    n=name.lower(); rules={'dance':['Stage and safe performance surface','PA system and microphones','Green room','Judges seating and score sheets','Backstage coordinator','Water and first-aid access'],'concert':['Stage and sound check','Power distribution and backup','Artist green room','Crowd entry/exit plan','Qualified technical crew','Weather protection for equipment'],'hackathon':['Power and extension boards','Wi-Fi and internet backup','Tables and chairs','Registration desk','Mentors and judges','Food and water'],'debate':['Microphones and projector','Judges table and timer','Seating plan','Topics and score sheets','Registration volunteers'],'exhibition':['Display tables and boards','Lighting and electrical safety','Visitor circulation route','Labels and signage','Display security'],'gaming':['Computers/consoles and displays','Stable power and network','Spare controllers/peripherals','Tournament brackets','Technical support']}
    items=[]
    for k,v in rules.items():
        if k in n:items.extend(v)
    if not items:items=['Confirm venue and capacity','Prepare equipment list','Assign coordinator and volunteers','Arrange registration and signage','Confirm first-aid and safety arrangements']
    if req:items.append('Verify facilities: '+req)
    return items

# Budget estimate model. Venue quote is user-entered; all other rates are editable planning assumptions.
def budget_breakdown(venue_name, venue_quote, attendees, hours, food_pp, sound, stage, decor, security_pp, marketing, logistics, contingency_pct):
    rows=[('Venue rental / quoted fee',float(venue_quote)),('Sound & lighting',float(sound)),('Stage & production',float(stage)),('Decoration & branding',float(decor)),('Food & refreshments',float(food_pp)*int(attendees)),('Security & safety',float(security_pp)*int(attendees)),('Marketing & printing',float(marketing)),('Logistics & power backup',float(logistics))]
    subtotal=sum(v for _,v in rows); reserve=subtotal*float(contingency_pct)/100
    rows += [('Contingency reserve',reserve),('TOTAL ESTIMATED BUDGET',subtotal+reserve)]
    return pd.DataFrame(rows,columns=['Budget head','Estimated amount (₹)'])

# Agent tools: the LLM can choose and chain these tools. It cannot book a venue or assert availability.
def run_agent_tool(name,args):
    if name=='search_bengaluru_venues':
        df,err=geoapify_venue_search(args.get('query','event venue'),args.get('max_results',15))
        return {'error':err,'venues':df.head(15).to_dict('records')}
    if name=='make_event_checklist': return {'checklist':checklist(args.get('event_type','campus event'),args.get('requirements',''))}
    if name=='check_event_weather':
        rep,_=weather_forecast(args.get('latitude',BLR_CENTER[0]),args.get('longitude',BLR_CENTER[1]),args.get('event_date',str(date.today()+timedelta(days=3))),args.get('start_time','15:00'),args.get('end_time','20:00'))
        return rep
    if name=='estimate_event_budget':
        df=budget_breakdown(args.get('venue_name','Selected venue'),args.get('venue_quote_inr',0),args.get('attendees',500),args.get('duration_hours',6),args.get('food_per_person_inr',250),args.get('sound_lighting_inr',20000),args.get('stage_inr',15000),args.get('decor_inr',10000),args.get('security_per_person_inr',35),args.get('marketing_inr',8000),args.get('logistics_inr',10000),args.get('contingency_percent',10))
        return {'budget':df.to_dict('records')}
    return {'error':'Unknown tool'}

AGENT_TOOLS=[
 {'type':'function','function':{'name':'search_bengaluru_venues','description':'Search Geoapify Places for live event venue listings inside a strict 35 km Bengaluru boundary. Returns public listing data only, not rental price, capacity or booking availability.','parameters':{'type':'object','properties':{'query':{'type':'string'},'max_results':{'type':'integer','minimum':1,'maximum':20}},'required':['query']}}},
 {'type':'function','function':{'name':'make_event_checklist','description':'Create an event-specific operations checklist.','parameters':{'type':'object','properties':{'event_type':{'type':'string'},'requirements':{'type':'string'}},'required':['event_type']}}},
 {'type':'function','function':{'name':'check_event_weather','description':'Check weather forecast for an event time and coordinates.','parameters':{'type':'object','properties':{'latitude':{'type':'number'},'longitude':{'type':'number'},'event_date':{'type':'string'},'start_time':{'type':'string'},'end_time':{'type':'string'}},'required':['event_date']}}},
 {'type':'function','function':{'name':'estimate_event_budget','description':'Calculate a draft event budget. Venue quote and assumptions are estimates unless supplied by organizer.','parameters':{'type':'object','properties':{'venue_name':{'type':'string'},'venue_quote_inr':{'type':'number'},'attendees':{'type':'integer'},'duration_hours':{'type':'number'},'food_per_person_inr':{'type':'number'},'sound_lighting_inr':{'type':'number'},'stage_inr':{'type':'number'},'decor_inr':{'type':'number'},'security_per_person_inr':{'type':'number'},'marketing_inr':{'type':'number'},'logistics_inr':{'type':'number'},'contingency_percent':{'type':'number'}},'required':['attendees']}}}
]

def agent_reply(question, history):
    if not GROQ_API_KEY:return 'Agentic AI is not connected yet. Add your Groq API key to your environment or Streamlit secrets. You can still use live venue search, weather and the budget calculator directly in their workspaces.'
    try:
        from groq import Groq
        client=Groq(api_key=GROQ_API_KEY)
        system=('You are FestPilot, an agentic college-fest planning operator. Work only within Bengaluru and its surrounding 35 km search boundary. '
        'Use tools when live venue data, weather, checklists or budget calculations are needed. You may chain tools. '
        'Geoapify results are public place listings, NOT confirmed event availability, capacity, facilities or rental quotes. Never invent these. '
        'Label assumptions, ask for missing constraints, explain trade-offs, and ask the organizer to verify/approve before any real-world commitment. '
        'Do not claim to book, contact or reserve venues. Keep responses concise and actionable.')
        msgs=[{'role':'system','content':system}]+history[-8:]+[{'role':'user','content':question}]
        for _ in range(5):
            r=client.chat.completions.create(model='openai/gpt-oss-20b',messages=msgs,tools=AGENT_TOOLS,tool_choice='auto',temperature=.2)
            m=r.choices[0].message
            if not m.tool_calls:return m.content or 'I could not produce a plan. Please add more detail.'
            msgs.append({'role':'assistant','content':m.content or '', 'tool_calls':[tc.model_dump() for tc in m.tool_calls]})
            for tc in m.tool_calls:
                try:args=json.loads(tc.function.arguments or '{}'); result=run_agent_tool(tc.function.name,args)
                except Exception as e:result={'error':str(e)}
                msgs.append({'role':'tool','tool_call_id':tc.id,'content':json.dumps(result,ensure_ascii=False,default=str)[:12000]})
        return 'I completed several planning steps. Please narrow the request or ask me to summarize the results.'
    except Exception as e:return f'Agent error: {e}. Check your Groq key and model access.'

# ---------------- UI ----------------
st.markdown('''<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800;900&family=Playfair+Display:wght@500;600;700&display=swap');
html,body,[class*="css"]{font-family:'DM Sans',sans-serif}.stApp{background:#f5f4f0;color:#161616}.block-container{max-width:1480px;padding-top:.8rem;padding-bottom:3rem}#MainMenu,footer{visibility:hidden}
.topstrip{background:#101010;color:#fff;text-align:center;padding:9px;font-size:10px;font-weight:800;letter-spacing:1.5px;margin:-.8rem -1rem 0}.brandbar{background:#fff;border-bottom:1px solid #e7e5df;padding:18px 22px;display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}.brand{font-size:28px;font-weight:900;letter-spacing:-1.8px}.brand span{color:#bd9254}.brandtag{font-size:10px;letter-spacing:2px;color:#777;font-weight:700}
.hero{min-height:420px;border-radius:8px;padding:58px 6%;display:flex;align-items:center;color:#fff;background:linear-gradient(90deg,rgba(8,10,12,.94),rgba(8,10,12,.66),rgba(8,10,12,.15)),url('https://images.unsplash.com/photo-1459749411175-04bf5292ceea?auto=format&fit=crop&w=1900&q=90');background-size:cover;background-position:center}.hero-inner{max-width:700px}.kicker,.section-kicker{color:#ad8248;font-size:10px;font-weight:800;letter-spacing:2.5px;text-transform:uppercase}.hero h1{font-family:'Playfair Display',serif;font-size:clamp(42px,6vw,74px);line-height:1;letter-spacing:-2px;color:white;margin:14px 0}.hero p{font-size:16px;line-height:1.7;color:#eee;max-width:590px}.section-head{font-family:'Playfair Display',serif;font-size:34px;font-weight:600;color:#161616;margin:4px 0 8px}.subtle{color:#777;font-size:13px}.feature,.panel{background:#fff;border:1px solid #e9e6df;padding:20px;border-radius:7px;min-height:130px}.feature h4{font-size:14px;margin:10px 0 5px}.feature p{font-size:12px;color:#777;line-height:1.55;margin:0}.metric{background:#fff;border:1px solid #e9e6df;padding:16px;border-radius:7px}
div.stButton>button{border-radius:5px;border:1px solid #b58a50;background:#b58a50;color:white;font-weight:700;min-height:42px}div.stButton>button:hover{background:#171717;border-color:#171717;color:white}.stTextInput input,.stNumberInput input,.stSelectbox div[data-baseweb="select"]{background:#fff}.footer{border-top:1px solid #dedbd3;margin-top:45px;padding:22px 0;color:#777;font-size:11px;letter-spacing:.5px}
</style>''',unsafe_allow_html=True)
st.markdown('<div class="topstrip">THE INTELLIGENCE BEHIND EVERY GREAT CAMPUS EVENT · PLAN WITH CONFIDENCE</div>',unsafe_allow_html=True)
st.markdown('<div class="brandbar"><div><div class="brand">FEST<span>PILOT</span></div><div class="brandtag">EVENT INTELLIGENCE PLATFORM · BENGALURU EDITION</div></div><div style="font-size:11px;font-weight:800;letter-spacing:1.4px;color:#555">DISCOVER &nbsp;&nbsp; PLAN &nbsp;&nbsp; EXECUTE</div><div style="font-size:19px">✦</div></div>',unsafe_allow_html=True)
# --------------------------------
# FESTPILOT WORKSPACE NAVIGATION
# --------------------------------
PAGES = [
    'Home',
    'Venue Discovery',
    'Weather Intelligence',
    'AI Event Planner',
    'Smart Schedule',
    'Budget & Resources'
]

# Initialise navigation state
if 'page' not in st.session_state:
    st.session_state.page = 'Home'

if 'workspace_nav' not in st.session_state:
    st.session_state.workspace_nav = st.session_state.page

# Apply programmatic navigation BEFORE the selectbox is created.
# This prevents StreamlitWidgetAlreadyInstantiatedError.
if 'pending_workspace_nav' in st.session_state:
    pending_page = st.session_state.pop('pending_workspace_nav')
    if pending_page in PAGES:
        st.session_state.page = pending_page
        st.session_state.workspace_nav = pending_page


def sync_workspace():
    """Synchronise page state after a user changes the sidebar selectbox."""
    st.session_state.page = st.session_state.workspace_nav


def go(page):
    """Navigate without modifying a widget key after widget creation."""
    if page not in PAGES:
        return

    st.session_state.page = page
    st.session_state.pending_workspace_nav = page
    st.rerun()


with st.sidebar:
    st.markdown('### FESTPILOT')
    st.caption('Your event command centre')

    st.selectbox(
        'Workspace',
        PAGES,
        key='workspace_nav',
        on_change=sync_workspace
    )

    st.divider()
    st.caption('SEARCH REGION')
    st.write('Bengaluru + 35 km surrounding area')

    st.caption('LIVE DATA')
    st.write(
        'Geoapify Places'
        if GEOAPIFY_API_KEY
        else 'Geoapify Places key not configured'
    )

nav = st.session_state.page

if nav=='Home':
    st.markdown('''<div class="hero"><div class="hero-inner"><div class="kicker">DISCOVER · DESIGN · DELIVER</div><h1>Make every<br>moment <i>matter.</i></h1><p>Your Bengaluru campus-fest command centre. Find real venue listings, turn event briefs into plans, prepare for weather and build a venue-linked budget.</p></div></div>''',unsafe_allow_html=True)
    st.markdown('<div style="height:25px"></div><div class="section-kicker">START WITH YOUR NEXT DECISION</div><div class="section-head">What are you planning today?</div><div class="subtle">Choose a workflow. FestPilot will carry your selections into the next planning step.</div>',unsafe_allow_html=True)
    a,b,c=st.columns(3)
    with a:
        st.markdown('<div class="feature"><h4>⌕ Discover a venue</h4><p>Search live OpenStreetMap listings around Bengaluru and shortlist a space.</p></div>',unsafe_allow_html=True)
        if st.button('Explore venues →',key='home_venue',use_container_width=True):go('Venue Discovery')
    with b:
        st.markdown('<div class="feature"><h4>✧ Build my event plan</h4><p>Ask the AI agent to search, check, estimate and prepare your event workflow.</p></div>',unsafe_allow_html=True)
        if st.button('Open AI planner →',key='home_ai',use_container_width=True):go('AI Event Planner')
    with c:
        st.markdown('<div class="feature"><h4>◈ Calculate my budget</h4><p>Choose a venue, enter its quote and generate an editable cost breakdown.</p></div>',unsafe_allow_html=True)
        if st.button('Plan my budget →',key='home_budget',use_container_width=True):go('Budget & Resources')
    st.markdown('<div style="height:25px"></div><div class="section-kicker">THE PLANNING LOOP</div><div class="section-head">One connected workflow.</div>',unsafe_allow_html=True)
    cols=st.columns(4)
    for col,(n,t,d) in zip(cols,[('01','Discover','Live listings and suitability checks'),('02','Plan','AI requirements and checklists'),('03','Protect','Weather-aware contingencies'),('04','Control','Schedule, resources and budget')]):
        with col:st.markdown(f'<div class="feature"><div class="kicker">{n}</div><h4>{t}</h4><p>{d}</p></div>',unsafe_allow_html=True)
    st.markdown('<div class="footer">FESTPILOT / EVENT INTELLIGENCE, REIMAGINED.</div>',unsafe_allow_html=True)

elif nav=='Venue Discovery':
    st.markdown('<div class="section-kicker">BENGALURU VENUE INTELLIGENCE</div><div class="section-head">Find a space that fits your fest.</div><p class="subtle">Fresh Geoapify Places search, restricted to a 35 km radius around central Bengaluru. Listing presence does not confirm event suitability or availability.</p>',unsafe_allow_html=True)
    with st.form('venue_search_form'):
        q=st.text_input('What kind of venue are you looking for?',value='college event auditorium OR convention centre',placeholder='e.g. outdoor event lawn, auditorium, convention centre')
        c1,c2=st.columns(2); maxres=c1.slider('Maximum results',5,20,15); c2.caption('Search region is fixed to Bengaluru + nearby areas.')
        submitted=st.form_submit_button('Search live venues →',use_container_width=True)
    if submitted:
        with st.spinner('Searching Geoapify Places inside the Bengaluru boundary…'):
            df,err=geoapify_venue_search(q,maxres); st.session_state.live_venues=df; st.session_state.live_venue_query=q
        if err:st.warning(err)
        elif not df.empty:st.success(f'{len(df)} live listing(s) found. Select one to carry into your budget.')
    df=st.session_state.get('live_venues',pd.DataFrame())
    if not df.empty:
        st.dataframe(df[['Venue','Type','Address','Phone','Distance from central Bengaluru (km)','Source','Map']],use_container_width=True,hide_index=True,column_config={'Map':st.column_config.LinkColumn('OpenStreetMap')})
        choices={f"{r['Venue']} · {r['Address'][:55]}":r for _,r in df.iterrows()}
        chosen=st.selectbox('Select a venue to plan around',list(choices.keys()))
        if st.button('Use this venue in Budget & Resources →',use_container_width=True):
            r=choices[chosen]; st.session_state.selected_venue={'name':r['Venue'],'address':r['Address'],'place_id':r['Place ID'],'maps_url':r['Map'],'source':'Geoapify Places','quote':0,'latitude':r['Latitude'],'longitude':r['Longitude']}; go('Budget & Resources')
        st.caption('Geoapify place listings are retrieved live for this search session. Map links open OpenStreetMap coordinates. Rental price, event capacity, facilities and date-specific booking availability must be confirmed directly with the venue. Geoapify data is subject to OpenStreetMap Platform terms and attribution requirements.')
    elif not GEOAPIFY_API_KEY:st.info('Configure a OpenStreetMap Platform API key in the notebook setup cell to enable live search. Uploaded spreadsheet venues remain available only as a fallback.')
    if not venues.empty:
        with st.expander('Also search my uploaded campus / verified venue workbook'):
            x,y,z=st.columns(3); city=x.text_input('Workbook city filter','Bengaluru'); typ=y.selectbox('Type',['Any','Indoor','Outdoor']); cap=z.number_input('Minimum capacity',0,100000,500,100)
            maxb=st.number_input('Maximum quoted / estimated rent (₹)',0,10000000,100000,5000); req=st.text_input('Required facilities separated by ;','Stage; Sound system')
            local=venue_search_local(city,typ,cap,maxb,req)
            if not local.empty:st.dataframe(local,use_container_width=True,hide_index=True)
            else:st.caption('No matching workbook rows. Workbook records are not live OpenStreetMap results.')

elif nav=='Weather Intelligence':
    st.markdown('<div class="section-kicker">PLAN FOR THE ELEMENTS</div><div class="section-head">Forecast-aware event planning.</div><p class="subtle">Check the selected venue coordinates and event window. Forecasts are limited to the available forecast horizon.</p>',unsafe_allow_html=True)
    sel=st.session_state.get('selected_venue',{})
    if not venues.empty: names=venues.venue_name.astype(str).tolist()
    else:names=[]
    if sel.get('name'):names=[sel['name']]+[n for n in names if n!=sel['name']]
    if names:
        chosen=st.selectbox('Venue',names); row=venues[venues.venue_name.astype(str)==chosen].iloc[0] if not venues.empty and chosen in venues.venue_name.astype(str).values else None
        lat=colval(row,'latitude',BLR_CENTER[0]) if row is not None else sel.get('latitude',BLR_CENTER[0]); lon=colval(row,'longitude',BLR_CENTER[1]) if row is not None else sel.get('longitude',BLR_CENTER[1])
    else:lat=st.number_input('Latitude',value=BLR_CENTER[0]);lon=st.number_input('Longitude',value=BLR_CENTER[1])
    x,y,z=st.columns(3); day=x.date_input('Event date',value=date.today()+timedelta(days=3),min_value=date.today()); start=y.text_input('Start time','15:00'); end=z.text_input('End time','20:00')
    if st.button('Assess event weather →'):
        report,hourly=weather_forecast(lat,lon,day,start,end); st.session_state.weather_report=report
        st.subheader(report.get('status','Forecast status'))
        if 'rain_probability' in report:
            a,b,c,d=st.columns(4);a.metric('Rain probability',f"{report['rain_probability']}%");b.metric('Rainfall',f"{report['rainfall_mm']} mm");c.metric('Max wind',f"{report['max_wind_kmh']} km/h");d.metric('Max temperature',f"{report['max_temperature_c']} °C")
        if not hourly.empty:st.line_chart(hourly.set_index('time')[['precipitation_probability','wind_speed_10m']])
        for item in report.get('recommendations',[]):st.write('☐ '+item)
    st.caption('Planning indicator only. Follow official IMD warnings, venue procedures and qualified safety advice.')

elif nav=='AI Event Planner':
    st.markdown('<div class="section-kicker">YOUR AGENTIC PLANNING PARTNER</div><div class="section-head">From brief to event blueprint.</div><p class="subtle">The agent can call venue search, weather, checklist and budget tools. It cannot book venues or verify availability.</p>',unsafe_allow_html=True)
    with st.expander('Give the agent your event brief',expanded=True):
        a,b,c=st.columns(3); event_type=a.text_input('Event type','College cultural fest'); attendees=b.number_input('Expected attendees',1,100000,1500,100); event_date=c.date_input('Event date',value=date.today()+timedelta(days=20),min_value=date.today())
        req=st.text_input('Must-have requirements','Stage, sound system, power backup, food, parking'); budget_cap=st.number_input('Total budget ceiling (₹)',0,100000000,300000,10000)
        if st.button('Ask agent to plan this event →',use_container_width=True):
            prompt=f'Plan a {event_type} in Bengaluru for {attendees} attendees on {event_date}, requirements: {req}, total budget ceiling {budget_cap} INR. Search live venues, create a checklist, and outline budget assumptions.'
            st.session_state.agent_prompt=prompt
    if 'chat' not in st.session_state:st.session_state.chat=[]
    for m in st.session_state.chat:
        with st.chat_message(m['role']):st.markdown(m['content'])
    prompt=st.chat_input('Ask: Find outdoor venues for 1,500 people, compare options and estimate costs…')
    if st.session_state.get('agent_prompt'):
        prompt=st.session_state.pop('agent_prompt')
    if prompt:
        history=st.session_state.chat.copy(); st.session_state.chat.append({'role':'user','content':prompt})
        with st.chat_message('user'):st.markdown(prompt)
        with st.chat_message('assistant'):
            with st.spinner('FestPilot is planning and calling tools…'):answer=agent_reply(prompt,history)
            st.markdown(answer)
        st.session_state.chat.append({'role':'assistant','content':answer})
    if st.button('Clear conversation'):st.session_state.chat=[];st.rerun()

elif nav=='Smart Schedule':
    st.markdown('<div class="section-kicker">ORCHESTRATE EVERY MOMENT</div><div class="section-head">A clearer view of your fest.</div><p class="subtle">Create a draft timetable from the uploaded workbook. This baseline is not a full multi-resource optimizer.</p>',unsafe_allow_html=True)
    if not events.empty and not venues.empty:
        if st.button('Generate draft timetable →'):
            scheduled=[]; issues=[]
            for _,e in events.iterrows():
                day=str(colval(e,'fest_day','Day 1')); name=str(colval(e,'event_name','Event')); cap=float(colval(e,'minimum_capacity',colval(e,'expected_attendance',0)) or 0)
                candidates=venues[pd.to_numeric(venues.capacity,errors='coerce').fillna(0)>=cap]
                if candidates.empty:issues.append(f'{name}: no venue with enough listed capacity.');continue
                v=candidates.iloc[0];scheduled.append({'Day':day,'Event':name,'Draft venue':colval(v,'venue_name'),'Status':'Needs organizer confirmation'})
            st.session_state.schedule=pd.DataFrame(scheduled);st.session_state.schedule_issues=issues
        if 'schedule' in st.session_state:st.dataframe(st.session_state.schedule,use_container_width=True,hide_index=True)
        for issue in st.session_state.get('schedule_issues',[]):st.warning(issue)
    else:st.info('Upload Events and Venues sheets to use the draft scheduler.')

elif nav=='Budget & Resources':
    st.markdown('<div class="section-kicker">CONTROL THE DETAILS</div><div class="section-head">A budget built around your chosen venue.</div><p class="subtle">Choose a live-discovered or workbook venue, enter the quote you received, then adjust every planning assumption.</p>',unsafe_allow_html=True)
    selected=st.session_state.get('selected_venue',{})
    names=[]
    if selected.get('name'):names.append(selected['name'])
    if not venues.empty:names += [n for n in venues.venue_name.astype(str).tolist() if n not in names]
    if names:
        chosen=st.selectbox('Selected venue',names,index=0)
        if chosen!=selected.get('name'):selected={'name':chosen,'source':'Uploaded workbook','quote':0};st.session_state.selected_venue=selected
    else:chosen=st.text_input('Venue name','My selected venue');selected={'name':chosen,'source':'Manual entry','quote':0};st.session_state.selected_venue=selected
    st.markdown(f"**Venue:** {selected.get('name',chosen)}  ·  **Source:** {selected.get('source','Manual entry')}" )
    if selected.get('address'):st.caption(selected['address'])
    if selected.get('maps_url'):st.markdown(f"[Open selected venue in OpenStreetMap]({selected['maps_url']})")
    a,b,c=st.columns(3); attendees=a.number_input('Expected attendees',1,100000,1500,100); hours=b.number_input('Event duration (hours)',1,72,8); quote=c.number_input('Venue quote / rental fee (₹)',0,100000000,int(selected.get('quote',0) or 0),5000)
    st.markdown('### Customize the cost assumptions')
    a,b,c=st.columns(3); food=a.number_input('Food per person (₹)',0,10000,250,25); sound=b.number_input('Sound & lighting (₹)',0,10000000,30000,2500); stage=c.number_input('Stage & production (₹)',0,10000000,25000,2500)
    a,b,c=st.columns(3); decor=a.number_input('Decoration & branding (₹)',0,10000000,15000,1000); security=b.number_input('Security per person (₹)',0,5000,35,5); marketing=c.number_input('Marketing & printing (₹)',0,10000000,10000,1000)
    a,b=st.columns(2); logistics=a.number_input('Logistics & power backup (₹)',0,10000000,12000,1000); contingency=b.slider('Contingency reserve (%)',0,30,10)
    estimate=budget_breakdown(chosen,quote,attendees,hours,food,sound,stage,decor,security,marketing,logistics,contingency)
    total=float(estimate.iloc[-1,1]); st.markdown('### Your venue-specific estimate')
    x,y,z=st.columns(3);x.metric('Estimated total',money(total));y.metric('Per attendee',money(total/max(attendees,1)));z.metric('Venue quote entered',money(quote))
    st.dataframe(estimate,use_container_width=True,hide_index=True)
    st.caption('Only the venue quote is intended to be a venue-specific price. Other figures are editable planning assumptions, not supplier quotes or Geoapify data.')
    csv=estimate.to_csv(index=False).encode('utf-8');st.download_button('Download budget breakdown (CSV)',csv,file_name='festpilot_venue_budget.csv',mime='text/csv')
    if not resources.empty:
        st.markdown('### Resource inventory');st.dataframe(resources,use_container_width=True,hide_index=True)
    if not budget.empty:
        with st.expander('Original uploaded budget sheet'):st.dataframe(budget,use_container_width=True,hide_index=True)

st.markdown('<div class="footer">FESTPILOT · BENGALURU-ONLY VENUE SEARCH · VERIFY QUOTES, CAPACITY AND AVAILABILITY BEFORE BOOKING.</div>',unsafe_allow_html=True)
