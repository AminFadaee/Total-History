"""Facts the outside reviews checked, kept as tests. A case still open is marked as an expected failure (strict, so
fixing it fails the run until its mark is removed)."""
import pytest

KEY_FIGURES_ABSENT = {
    "Republic of Austria": ["Sigmund Freud"], "South Africa": ["Robert Sobukwe"], "Beylik of Karaman": ["Khalji", "Tughluq"],
    "Avar Khaganate": ["Theodore Abu Qurrah"], "Nguyễn dynasty": ["Ngo Dinh Diem"], "Kingdom of Portugal": ["Salazar"],
    "Kingdom of Castile": ["El Cid"], "Safavid Dynasty": ["Nader Shah"], "Qajar Dynasty": ["Reza Shah"],
    "Parthian Empire": ["Gondophares"], "Kingdom of Judah": ["Hazael"], "Majapahit": ["Gajah Mada"],
    "Western Roman Empire": ["Ricimer"], "Empire of Brazil": ["Teresa Cristina"], "Zengid dynasty": ["Ahmad Sanjar"],
    "Tokugawa Shogunate": ["Emperor Go-Yōzei", "Emperor Meiji"], "Kamakura Shogunate": ["Emperor Go-Toba", "Emperor Go-Daigo"],
    "Ashikaga Shogunate": ["Emperor Kōgon", "Emperor Ōgimachi"], "Hittites": ["Šuppiluliuma II"],
    "Old Kingdom of Egypt": ["Netjerkare"], "Ashanti Empire": ["Prempeh II"], "Kingdom of Navarre": ["Louis XIII"],
    "Kingdom of Bohemia": ["Francis II"], "Kingdom of Sweden": ["Christian I of Denmark"], "Habsburg Monarchy": ["Philip II of Spain"],
    "Kingdom of Galicia–Volhynia": ["Casimir III"], "Kingdom of Hungary": ["Szálasi"], "Prussia": ["Friedrich Ebert"],
    "Sultanate of Rum": ["Ertuğrul"], "United States of America": ["Ronald Reagan", "Obama"],
}
KEY_FIGURES_PRESENT = {
    "Mamluk Sultanate": ["Baybars", "Qalawun"], "Afsharid Iran": ["Nader Shah"], "Pahlavi Dynasty": ["Reza Shah"],
    "Kingdom of Italy": ["Victor Emmanuel II"], "Ottoman Empire": ["Suleiman"], "Mughal Empire": ["Akbar"], "Qing Dynasty": ["Kangxi"],
    "Mongol Empire": ["Genghis Khan"], "Tokugawa Shogunate": ["Tokugawa Ieyasu"], "Kamakura Shogunate": ["Minamoto no Yoritomo"],
    "Ashikaga Shogunate": ["Ashikaga Takauji", "Yoshimitsu"], "Babylonia": ["Hammurabi"], "New Kingdom of Egypt": ["Ramesses II"],
    "Lydia": ["Croesus"], "Maratha Empire": ["Shivaji"], "Achaemenid Empire": ["Cyrus"], "Roman Empire": ["Augustus"],
    "Union of Soviet Socialist Republics": ["Lenin"], "Russian Empire": ["Peter the Great", "Catherine"], "Kingdom of France": ["Louis XIV", "Louis IX"],
    "Abbasid Caliphate": ["Harun al-Rashid"], "Tang Dynasty": ["Taizong"], "Inca Empire": ["Pachacuti"], "Timurid Empire": ["Timur"],
    "Kingdom of Scotland": ["Robert the Bruce", "David I"], "Kingdom of Hungary": ["Stephen I", "Matthias Corvinus"],
    "Neo-Assyrian Empire": ["Sennacherib", "Ashurbanipal", "Sargon II"], "Crown of Aragon": ["James I"], "Kingdom of Aragon": ["James I"],
    "Crown of Castile": ["Isabella I"], "Zengid dynasty": ["Zengi"], "Old Kingdom of Egypt": ["Khufu", "Djoser"],
    "Classical Athens": ["Pericles"], "Carthage": ["Hannibal"], "Maya civilization": ["Pakal"], "Golden Horde": ["Batu", "Özbeg", "Uzbeg"],
    "Ilkhanate": ["Hulegu", "Ghazan"], "British Raj": ["Victoria"], "Spanish Empire": ["Philip II", "Charles V"], "Yugoslavia": ["Alexander I"],
    "Göktürks": ["Bumin", "Bilge"], "Holy Roman Empire": ["Otto I", "Charles V"],
}
SUCCESSION_ABSENT = [
    ("Lanna Kingdom", "after", "Konbaung Dynasty"), ("Saadi Sultanate", "after", "Republic of Salé"),
    ("Tang Dynasty", "after", "Former Shu"), ("Abbasid Caliphate", "after", "Fatimid Caliphate"),
    ("Holy Roman Empire", "after", "Grand Duchy of Hesse"), ("Holy Roman Empire", "after", "French First Republic"),
    ("Kingdom of Navarre", "after", "Crown of Aragon"), ("Kingdom of Hungary", "after", "Ottoman Empire"),
    ("Tenochtitlan", "after", "Aztec Triple Alliance"), ("Assyria", "after", "Neo-Assyrian Empire"),
    ("Qin dynasty", "before", "Qi"), ("Prussia", "after", "German Confederation"), ("Inca Empire", "before", "Chimor"),
    ("Sui Dynasty", "before", "Chen Dynasty"), ("Majapahit", "before", "Srivijaya"), ("Phoenicia", "before", "New Kingdom of Egypt"),
]
SUCCESSION_PRESENT = [
    ("Ming Dynasty", "after", "Qing Dynasty"), ("Mughal Empire", "before", "Delhi Sultanate"), ("Delhi Sultanate", "after", "Mughal Empire"),
    ("Abbasid Caliphate", "after", "Mongol Empire"), ("Kingdom of Kush", "after", "Makuria"), ("Sasanian Empire", "before", "Parthian Empire"),
    ("Roman Empire", "after", "Western Roman Empire"), ("Mali Empire", "after", "Songhai Empire"), ("Shang Dynasty", "after", "Zhou Dynasty"),
    ("Prussia", "before", "Margraviate of Brandenburg"), ("Austria-Hungary", "before", "Austrian Empire"),
]
SUCCESSION_ANSWERED = [("Carthage", "after"), ("Tang Dynasty", "after"), ("Song dynasty", "before"), ("Babylonia", "after"),
                       ("Zhou Dynasty", "after"), ("Seleucid Empire", "after"), ("Ptolemaic Kingdom", "after"), ("Gupta Empire", "before")]
HOME_REGION = {"Roman Empire": "Italy", "Austria-Hungary": "Austria", "German Confederation": "Germany", "Prussia": "Germany"}
CAPITALS_ABSENT = {"Abbasid Caliphate": ["Cairo"], "Maurya Empire": ["Patna"], "Göktürks": ["Ordu-Baliq"], "Pala Empire": ["Murshidabad"],
                   "Brazilian Republic": ["Brasília"]}
CAPITALS_PRESENT = {"Republic of Venice": ["Venice"], "Khmer Empire": ["Angkor"], "Kingdom of Romania": ["Bucharest"],
                    "Vichy France": ["Vichy"], "Ottoman Empire": ["Constantinople"], "Roman Empire": ["Rome"], "Abbasid Caliphate": ["Baghdad"],
                    "Kingdom of France": ["Paris"], "Aztec Triple Alliance": ["Tenochtitlan"], "Heian Japan": ["Heian-kyō", "Kyoto"]}
IN_DECK = ["Maya civilization", "Indus Valley Civilization", "Minoan civilization", "Delhi Sultanate", "Chola dynasty", "Song dynasty",
           "Canada", "Argentine Republic", "Heian Japan", "Kamakura Shogunate", "Shu Han", "Classical Athens", "Teotihuacan",
           "Olmec Civilization", "Sumerian City-States", "Median Empire", "Benin Empire", "Great Zimbabwe", "Serbian Empire"]
TIERS = {"Roman Empire": "Essential", "Achaemenid Empire": "Essential", "Classical Athens": "Essential", "Aztec Triple Alliance": "Essential",
         "Hunnic Empire": "Core", "Rashidun Caliphate": "Core", "Ilkhanate": "Core", "Scandinavian minor kingdoms": "Extended",
         "Vandals": "Extended", "Kingdom of Fiji": "Extended", "Cornwall": "Extended"}
PERIOD_ENDS_BY = {"Western Jin": 320, "Kingdom of Castile": 1250, "Kingdom of León": 1250}
PERIOD_PRESENT = ["Maya civilization", "Minoan civilization", "Olmec Civilization", "Teotihuacan", "Hunnic Empire", "Classical Athens"]
WHAT_PRESENT = ["British Empire", "Etruscans", "Teotihuacan", "Principality of Wallachia", "Principality of Rostov-Suzdal"]

KNOWN_OPEN = {
    'capital-absent:Göktürks/Ordu-Baliq',
    'capital-absent:Pala Empire/Murshidabad',
    'capital-present:Aztec Triple Alliance/Tenochtitlan',
    'capital-present:Heian Japan/Heian-kyō|Kyoto',
    'figure-absent:Avar Khaganate/Theodore Abu Qurrah',
    'figure-absent:Beylik of Karaman/Khalji',
    'figure-absent:Empire of Brazil/Teresa Cristina',
    'figure-absent:Habsburg Monarchy/Philip II of Spain',
    'figure-absent:Kingdom of Galicia–Volhynia/Casimir III',
    'figure-absent:Kingdom of Hungary/Szálasi',
    'figure-absent:Kingdom of Sweden/Christian I of Denmark',
    'figure-absent:Prussia/Friedrich Ebert',
    'figure-absent:Sultanate of Rum/Ertuğrul',
    'figure-present:British Raj/Victoria',
    'figure-present:Golden Horde/Batu|Özbeg|Uzbeg',
    'figure-present:Göktürks/Bumin|Bilge',
    'figure-present:Spanish Empire/Philip II|Charles V',
    'figure-present:Yugoslavia/Alexander I',
    'in-deck:Benin Empire',
    'in-deck:Great Zimbabwe',
    'in-deck:Median Empire',
    'in-deck:Serbian Empire',
    'period-ends-by:Kingdom of Castile/1250',
    'period-ends-by:Kingdom of León/1250',
    'period-ends-by:Western Jin/320',
    'period-present:Hunnic Empire',
    'period-present:Maya civilization',
    'period-present:Minoan civilization',
    'period-present:Olmec Civilization',
    'period-present:Teotihuacan',
    'succession-absent:Assyria/after/Neo-Assyrian Empire',
    'succession-absent:Holy Roman Empire/after/French First Republic',
    'succession-absent:Inca Empire/before/Chimor',
    'succession-absent:Kingdom of Hungary/after/Ottoman Empire',
    'succession-absent:Kingdom of Navarre/after/Crown of Aragon',
    'succession-absent:Lanna Kingdom/after/Konbaung Dynasty',
    'succession-absent:Majapahit/before/Srivijaya',
    'succession-absent:Phoenicia/before/New Kingdom of Egypt',
    'succession-absent:Prussia/after/German Confederation',
    'succession-absent:Qin dynasty/before/Qi',
    'succession-absent:Sui Dynasty/before/Chen Dynasty',
    'succession-absent:Tenochtitlan/after/Aztec Triple Alliance',
    'succession-answered:Babylonia/after',
    'succession-answered:Carthage/after',
    'succession-answered:Ptolemaic Kingdom/after',
    'succession-answered:Seleucid Empire/after',
    'succession-answered:Song dynasty/before',
    'succession-answered:Tang Dynasty/after',
    'succession-answered:Zhou Dynasty/after',
    'succession-present:Kingdom of Kush/after/Makuria',
    'succession-present:Mali Empire/after/Songhai Empire',
    'tier:Aztec Triple Alliance/Essential',
    'tier:Ilkhanate/Core',
    'tier:Rashidun Caliphate/Core',
    'tier:Scandinavian minor kingdoms/Extended',
    'what-present:British Empire',
    'what-present:Etruscans',
    'what-present:Principality of Rostov-Suzdal',
    'what-present:Principality of Wallachia',
    'what-present:Teotihuacan',
}


def cases(name, items):
    return [pytest.param(*item, id=f"{name}:{'/'.join(map(str, item))}",
                         marks=pytest.mark.xfail(strict=True) if f"{name}:{'/'.join(map(str, item))}" in KNOWN_OPEN else ())
            for item in items]


def figure_names(civilizations, civilization):
    return " | ".join(figure["name"] for figure in civilizations[civilization]["key_figures"]).lower()


def answers(civilizations, civilization, side):
    return [edge["name"] for edge in civilizations[civilization][side]]


@pytest.mark.parametrize("civilization, person", cases("figure-absent", [(c, p) for c, ps in KEY_FIGURES_ABSENT.items() for p in ps]))
def test_key_figure_absent(civilizations, civilization, person):
    assert person.lower() not in figure_names(civilizations, civilization)


@pytest.mark.parametrize("civilization, people", cases("figure-present", [(c, "|".join(ps)) for c, ps in KEY_FIGURES_PRESENT.items()]))
def test_key_figure_present(civilizations, civilization, people):
    assert any(person.lower() in figure_names(civilizations, civilization) for person in people.split("|"))


@pytest.mark.parametrize("civilization, side, other", cases("succession-absent", SUCCESSION_ABSENT))
def test_succession_absent(civilizations, civilization, side, other):
    assert other not in answers(civilizations, civilization, side)


@pytest.mark.parametrize("civilization, side, other", cases("succession-present", SUCCESSION_PRESENT))
def test_succession_present(civilizations, civilization, side, other):
    assert other in answers(civilizations, civilization, side)


@pytest.mark.parametrize("civilization, side", cases("succession-answered", SUCCESSION_ANSWERED))
def test_succession_answered(civilizations, civilization, side):
    assert answers(civilizations, civilization, side)


@pytest.mark.parametrize("civilization, region", cases("home-region", list(HOME_REGION.items())))
def test_home_region(civilizations, civilization, region):
    homes = [edge["region"] for side in ("before", "after") for edge in civilizations[civilization][side] if edge.get("home")]
    assert homes and all(home == region for home in homes)


@pytest.mark.parametrize("civilization, capital", cases("capital-absent", [(c, p) for c, ps in CAPITALS_ABSENT.items() for p in ps]))
def test_capital_absent(civilizations, civilization, capital):
    assert capital not in [found["name"] for found in civilizations[civilization]["capitals"]]


@pytest.mark.parametrize("civilization, capitals", cases("capital-present", [(c, "|".join(ps)) for c, ps in CAPITALS_PRESENT.items()]))
def test_capital_present(civilizations, civilization, capitals):
    assert set(capitals.split("|")) & {found["name"] for found in civilizations[civilization]["capitals"]}


@pytest.mark.parametrize("civilization", cases("in-deck", [(c,) for c in IN_DECK]))
def test_in_deck(civilizations, civilization):
    assert civilization in civilizations


@pytest.mark.parametrize("civilization, tier", cases("tier", list(TIERS.items())))
def test_tier(civilizations, civilization, tier):
    assert civilizations[civilization]["tier"] == tier


@pytest.mark.parametrize("civilization, year", cases("period-ends-by", list(PERIOD_ENDS_BY.items())))
def test_period_ends_by(civilizations, civilization, year):
    period = civilizations[civilization]["period"]
    assert not period["confirmed"] or period["end"]["latest"] <= year


@pytest.mark.parametrize("civilization", cases("period-present", [(c,) for c in PERIOD_PRESENT]))
def test_period_present(civilizations, civilization):
    period = civilizations[civilization]["period"]
    assert period["confirmed"] or period.get("core") or period.get("since")


@pytest.mark.parametrize("civilization", cases("what-present", [(c,) for c in WHAT_PRESENT]))
def test_what_line_present(civilizations, civilization):
    assert civilizations[civilization].get("summary")
