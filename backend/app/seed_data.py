"""Seed complaints: realistic, Urdu-influenced English, spread across every category.

Each entry has a stable `key`. The seed command derives the row's UUID from it, which is what
makes running the seed twice a no-op. Never change or reuse a key once it has been seeded.

Rows are recorded as triaged_by="rules": they never went through an LLM, and should not claim to.
"""

from dataclasses import dataclass

from app.domain import Category, Priority, Status


@dataclass(frozen=True)
class SeedComplaint:
    key: str
    text: str
    location: str
    category: Category
    priority: Priority
    summary: str
    status: Status = Status.OPEN
    days_ago: int = 0
    contact: str | None = None


C, P, S = Category, Priority, Status

SEED_COMPLAINTS: tuple[SeedComplaint, ...] = (
    # ---- water ----
    SeedComplaint(
        "water-01",
        "Burst water main flooding Street 12 since fajr, water entering ground floors of "
        "three houses. Please send team urgently.",
        "Street 12, Block C",
        C.WATER,
        P.HIGH,
        "Burst water main flooding Street 12 and entering homes",
        S.IN_PROGRESS,
        1,
        "0300-1112233",
    ),
    SeedComplaint(
        "water-02",
        "Pani ki supply band hai since two days in our mohalla. Tanker walay bhi 2000 rupay "
        "maang rahay hain, garib log kya karein.",
        "Mohalla Islamabad Colony",
        C.WATER,
        P.HIGH,
        "No water supply for two days; residents forced to buy tankers",
        S.OPEN,
        2,
    ),
    SeedComplaint(
        "water-03",
        "Tap water is coming muddy brown and smells bad. My kids fell sick after drinking it, "
        "please test the line.",
        "House 44, Gulberg Road",
        C.WATER,
        P.HIGH,
        "Muddy, foul-smelling tap water making children sick",
        S.OPEN,
        3,
        "0321-4455667",
    ),
    SeedComplaint(
        "water-04",
        "Water pressure is very low every evening after maghrib, upper floors get nothing at all.",
        "Sector F-8 Market Lane",
        C.WATER,
        P.NORMAL,
        "Very low evening water pressure; upper floors get none",
        S.OPEN,
        5,
    ),
    SeedComplaint(
        "water-05",
        "Small leakage from the main pipe near the masjid gate, water is wasting all day but it "
        "is not flooding the road.",
        "Near Jamia Masjid, Bazaar Road",
        C.WATER,
        P.LOW,
        "Small pipe leak wasting water near the masjid gate",
        S.RESOLVED,
        9,
    ),
    SeedComplaint(
        "water-06",
        "New water connection was paid for in Ramzan but still no line has come to our house, "
        "office keeps saying next week.",
        "Street 7, Phase 2",
        C.WATER,
        P.LOW,
        "Paid-for water connection still not installed",
        S.REJECTED,
        14,
        "0345-9988776",
    ),
    # ---- electricity ----
    SeedComplaint(
        "elec-01",
        "Electric wire has fallen on the road near the school gate and is sparking. Children "
        "pass here daily, someone will get hurt.",
        "Government Girls School, Street 3",
        C.ELECTRICITY,
        P.HIGH,
        "Live wire down and sparking beside a school gate",
        S.IN_PROGRESS,
        0,
    ),
    SeedComplaint(
        "elec-02",
        "Transformer blew with a loud bang last night and the whole mohalla is without bijli "
        "since then. Old people and a dialysis patient here.",
        "Mohalla Shah Faisal",
        C.ELECTRICITY,
        P.HIGH,
        "Blown transformer; whole neighbourhood without power overnight",
        S.OPEN,
        1,
        "0333-2211445",
    ),
    SeedComplaint(
        "elec-03",
        "Load shedding is happening 8 to 10 hours daily, not the announced schedule. Shops are "
        "closing early and no one is answering the helpline.",
        "Main Bazaar, Sector G-10",
        C.ELECTRICITY,
        P.NORMAL,
        "8-10 hours of daily outages, far beyond the announced schedule",
        S.OPEN,
        4,
    ),
    SeedComplaint(
        "elec-04",
        "Voltage keeps fluctuating, our fridge and AC compressor got damaged twice this month.",
        "House 12, Street 9, Block B",
        C.ELECTRICITY,
        P.NORMAL,
        "Voltage fluctuations damaging appliances",
        S.IN_PROGRESS,
        6,
    ),
    SeedComplaint(
        "elec-05",
        "Meter reading in my bill is double what we use. Meter inspector never came despite "
        "three applications.",
        "Flat 8, Al-Noor Apartments",
        C.ELECTRICITY,
        P.LOW,
        "Bill roughly double actual use; meter inspection never done",
        S.OPEN,
        11,
        "0300-7766554",
    ),
    SeedComplaint(
        "elec-06",
        "Electricity pole is leaning badly after the rain, tilted towards the shop. Please "
        "fix it before the next storm.",
        "Chowk Sabzi Mandi",
        C.ELECTRICITY,
        P.NORMAL,
        "Electricity pole leaning dangerously after rain",
        S.RESOLVED,
        12,
    ),
    # ---- sanitation ----
    SeedComplaint(
        "san-01",
        "Sewerage water is overflowing in gali number 5 for three days, it is entering the "
        "houses and children cannot go to school.",
        "Gali 5, Mohalla Rehmania",
        C.SANITATION,
        P.HIGH,
        "Sewage overflowing into homes for three days",
        S.OPEN,
        2,
        "0312-5566778",
    ),
    SeedComplaint(
        "san-02",
        "Open manhole without cover at the market entrance, a bike fell inside yesterday "
        "night. Very dangerous especially after dark.",
        "Market Entrance, Sector I-8",
        C.SANITATION,
        P.HIGH,
        "Uncovered manhole at market entrance; a motorbike fell in",
        S.IN_PROGRESS,
        1,
    ),
    SeedComplaint(
        "san-03",
        "Garbage has not been picked up for one week, kachra is piled next to the park and "
        "flies are everywhere.",
        "Park Road, Block D",
        C.SANITATION,
        P.NORMAL,
        "Garbage uncollected for a week beside the park",
        S.OPEN,
        4,
    ),
    SeedComplaint(
        "san-04",
        "Nali behind our street is completely blocked with plastic bags, stinking water is "
        "standing there and mosquitoes are increasing, dengue ka khatra hai.",
        "Street 15, Model Colony",
        C.SANITATION,
        P.NORMAL,
        "Blocked drain with standing water breeding mosquitoes",
        S.OPEN,
        7,
        "0301-8899001",
    ),
    SeedComplaint(
        "san-05",
        "Dustbin on the corner is broken and lid is missing, stray dogs spread the waste "
        "every morning.",
        "Corner of Street 4 and Main Road",
        C.SANITATION,
        P.LOW,
        "Broken dustbin; stray dogs scatter waste every morning",
        S.RESOLVED,
        10,
    ),
    SeedComplaint(
        "san-06",
        "Public toilet near the bus stand has been locked and dirty for months, there is no "
        "cleaner and no water at all.",
        "Bus Stand, GT Road",
        C.SANITATION,
        P.LOW,
        "Public toilet at bus stand locked, dirty and without water",
        S.REJECTED,
        16,
    ),
    # ---- roads ----
    SeedComplaint(
        "road-01",
        "Huge pothole in the middle of the main road near the flyover, two motorcycles "
        "slipped this week. Someone will die if this stays.",
        "Main Road below Flyover, Sector G-9",
        C.ROADS,
        P.HIGH,
        "Large pothole near flyover causing motorcycle accidents",
        S.OPEN,
        2,
        "0322-1100223",
    ),
    SeedComplaint(
        "road-02",
        "Road caved in after the rain and there is a deep hole now, cars are taking the wrong "
        "side to avoid it, accident hone wala hai.",
        "Street 21, Phase 1",
        C.ROADS,
        P.HIGH,
        "Road collapse after rain forcing cars onto the wrong side",
        S.IN_PROGRESS,
        3,
    ),
    SeedComplaint(
        "road-03",
        "Speed breaker in front of the hospital is too high, ambulances have to slow to a "
        "crawl and patients are shaken badly.",
        "Civil Hospital Road",
        C.ROADS,
        P.NORMAL,
        "Speed breaker at hospital too high for ambulances",
        S.OPEN,
        8,
    ),
    SeedComplaint(
        "road-04",
        "Road is broken for the last six months after the gas line digging, nobody has "
        "repaired it and dust is entering all shops.",
        "Bazaar Road, Old City",
        C.ROADS,
        P.NORMAL,
        "Road left unrepaired six months after gas line digging",
        S.OPEN,
        13,
        "0334-6677889",
    ),
    SeedComplaint(
        "road-05",
        "Faded zebra crossing and no signs near the primary school, parents are scared to "
        "cross with children.",
        "Primary School, Street 6",
        C.ROADS,
        P.LOW,
        "Faded zebra crossing near a primary school",
        S.RESOLVED,
        15,
    ),
    # ---- streetlights ----
    SeedComplaint(
        "light-01",
        "All streetlights on our whole street have been off for two weeks, there were two "
        "phone snatching incidents after isha.",
        "Street 9, Block A",
        C.STREETLIGHTS,
        P.HIGH,
        "Whole street unlit for two weeks; phone snatchings reported",
        S.OPEN,
        2,
        "0313-4400556",
    ),
    SeedComplaint(
        "light-02",
        "Streetlight outside the mosque is not working, namazis face problem in the dark "
        "going for fajr.",
        "Masjid Bilal, Street 2",
        C.STREETLIGHTS,
        P.NORMAL,
        "Streetlight out at the mosque; worshippers walk in the dark at fajr",
        S.IN_PROGRESS,
        5,
    ),
    SeedComplaint(
        "light-03",
        "Light pole near the park keeps flickering all night and it is very irritating, "
        "please change the bulb.",
        "Park Road, Sector F-7",
        C.STREETLIGHTS,
        P.LOW,
        "Flickering streetlight near the park",
        S.OPEN,
        9,
    ),
    SeedComplaint(
        "light-04",
        "Streetlight is ON in daytime the whole week, wasting bijli while we have load "
        "shedding at night. Timer is broken maybe.",
        "Main Boulevard, Phase 3",
        C.STREETLIGHTS,
        P.LOW,
        "Streetlight burning all day, likely a broken timer",
        S.RESOLVED,
        12,
    ),
    SeedComplaint(
        "light-05",
        "Two lamps at the underpass are broken and it is fully dark, women avoid this "
        "way now after maghrib.",
        "Underpass, Ring Road",
        C.STREETLIGHTS,
        P.NORMAL,
        "Broken lamps leave underpass dark; women avoid it after maghrib",
        S.OPEN,
        6,
    ),
    # ---- other ----
    SeedComplaint(
        "other-01",
        "Stray dogs pack of around ten is chasing children and bikers in our street, one "
        "child was bitten yesterday, please take action.",
        "Street 11, Block E",
        C.OTHER,
        P.HIGH,
        "Pack of stray dogs chasing children; one child bitten",
        S.OPEN,
        1,
        "0302-9911223",
    ),
    SeedComplaint(
        "other-02",
        "Illegal encroachment by shopkeepers, whole footpath is occupied and pedestrians "
        "have to walk on the main road.",
        "Sabzi Mandi Road",
        C.OTHER,
        P.NORMAL,
        "Shops blocking the footpath and pushing pedestrians onto the road",
        S.OPEN,
        10,
    ),
    SeedComplaint(
        "other-03",
        "Loudspeaker is being used late at night every weekend for functions until 2am, "
        "students and old people cannot sleep.",
        "Community Hall, Street 5",
        C.OTHER,
        P.LOW,
        "Loud late-night functions disturbing residents most weekends",
        S.REJECTED,
        17,
    ),
    SeedComplaint(
        "other-04",
        "Park benches are broken and the swings are rusted, small kids can get injured. Also "
        "the park gate is always locked on Sunday.",
        "Central Park, Sector G-8",
        C.OTHER,
        P.LOW,
        "Broken benches and rusted swings in the public park",
        S.RESOLVED,
        18,
        "0306-1234098",
    ),
)
