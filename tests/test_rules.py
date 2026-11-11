"""The pure rules the pipeline applies to source text, each on the cases that once went wrong or easily could."""
import pathlib

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import MultiPolygon, box

from total_history.births import Birth, agreed_birth
from total_history.dates import Span, centuries, parse, parse_range, period_label
from total_history.deck import CIVILIZATION_TEMPLATES, century_tag, with_modern_country
from total_history.errata import Erratum, legacy_rejections, passed_legacy
from total_history.figures import lifetime
from total_history.legacy import CIVILIZATION, FIGURE, LegacyScores, good_lines, hide, legacy_sections, name_forms, repeats
from total_history.photos import visible
from total_history.polities import DATED, NEIGHBOUR_ROOTS, REGIME_CHANGE, PolityBuilder, RulingRole, row_years, same_name
from total_history.sentences import contribution, first_paragraph, sentences
from total_history.sources.cliopatria import Cliopatria, holding, occupation
from total_history.sources.wikidata import YEAR_PRECISION, Entity, Year
from total_history.sources.wikipedia import cited


@pytest.mark.parametrize("text, years", [
    ("1192–1199", (1192, 1199)), ("1192", (1192, 1192)), ("r. 1192-1199", (1192, 1199)), ("{{reign|1192|1199}}", (1192, 1199)),
    ("c. 550 – 530 BC", (-550, -530)), ("560 BCE", (-560, -560)), ("27 BC – AD 14", (-27, 14)), ("AD 14 – 37", (14, 37)),
    ("1199–1192", (1199, 1199)), ("165/155–130 BC", (-165, -155)), ("no years", None),
])
def test_row_years(text, years):
    assert row_years(text) == years


@pytest.mark.parametrize("first, second, same", [
    ("Qin", "Qin dynasty", True), ("Kingdom of Castile", "Crown of Castile", True),
    ("Kingdom of Naples", "Kingdom of Naples (Napoleonic)", True), ("Roman Republic", "Roman Empire", False),
    ("Kingdom of England", "Kingdom of Scotland", False), ("Qin dynasty", "Qi", False),
])
def test_same_name(first, second, same):
    assert same_name(first, second) == same


@pytest.mark.parametrize("role, title, place, reign", [
    ("Founder and Emperor of Han Dynasty of China from 202 to 195 BC", "emperor", "China", (-202, -195)),
    ("King of France from 1643 to 1715", "king", "France", (1643, 1715)),
])
def test_ruling_role(role, title, place, reign):
    read = RulingRole.read(role)
    assert (read.title, read.place, read.reign) == (title, place, reign)


@pytest.mark.parametrize("role", ["Queen consort of France", "French philosopher"])
def test_roles_that_are_not_rule(role):
    assert RulingRole.read(role) is None


def years(*values: int) -> list[Year]:
    return [Year(value, YEAR_PRECISION, False) for value in values]


@pytest.mark.parametrize("birth, death, span", [
    (years(1638), years(1715), (1638, 1715)), ([], years(453), (383, 453)), (years(1205), [], (1205, 1275)),
    (years(-499), years(453), (383, 453)), ([], [], None), (years(-250), years(-257), (-327, -257)),
])
def test_lifetime(birth, death, span):
    assert lifetime(birth, death) == span


@pytest.mark.parametrize("number, tag", [
    (-6, "TH::Century::BC::06th"), (1, "TH::Century::AD::01st"), (3, "TH::Century::AD::03rd"), (11, "TH::Century::AD::11th"),
    (12, "TH::Century::AD::12th"), (13, "TH::Century::AD::13th"), (21, "TH::Century::AD::21st"),
])
def test_century_tag(number, tag):
    assert century_tag(number) == tag


def test_centuries_cross_the_era():
    assert centuries(-150, 150) == [-2, -1, 1, 2]


@pytest.mark.parametrize("text, label", [("1643", "1643"), ("202 BC", "202 BC")])
def test_parse_labels(text, label):
    assert parse(text).label() == label


@pytest.mark.parametrize("text, span", [
    ("370s", Span(370, 379)), ("c. 1430s", Span(1430, 1439, True)), ("370s BC", Span(-379, -370)),
    ("4 September 476", Span(476, 476)), ("April 13, 1375", Span(1375, 1375)),
])
def test_parse_decades_and_days(text, span):
    assert parse(text) == span


def test_period_label_across_the_era():
    start, end = parse_range("27 BC – AD 14")
    assert period_label(start, end) == "27 BC – AD 14"


def test_contribution_ends_at_a_stray_comma():
    paragraph = "Ibn Yunus (c. 950 – 1009) was an Arab and Egyptian astronomer and mathematician,. The Fatimids gave him gifts."
    assert contribution(paragraph, "Egyptian mathematician", None) == "An Arab and Egyptian astronomer and mathematician."


def test_contribution_spaces_words_once():
    paragraph = "Ugarit (/uːˈɡɑːrɪt/) was an ancient city in what is today northern Syria, at a site known in  Arabic as Ras Shamra."
    assert "  " not in contribution(paragraph, "Ugarit", None)


@pytest.mark.parametrize("paragraph, text", [
    ("The British Empire comprised the dominions, colonies and protectorates ruled by the United Kingdom.",
     "The dominions, colonies and protectorates ruled by the United Kingdom."),
    ("The New Kingdom, also called the Egyptian Empire, refers to ancient Egypt between the 16th century BC and the 11th "
     "century BC.", "Ancient Egypt between the 16th century BC and the 11th century BC."),
    ("The Dutch Republic, also referred to as the United Provinces, was a confederation and great power.", "A confederation and great power."),
])
def test_contribution_cuts_the_subject_at_its_verb(paragraph, text):
    assert contribution(paragraph, "", None) == text


@pytest.mark.parametrize("paragraph, text", [
    ("Shenouda III was the 117th Pope of Alexandria and Patriarch of the See of St. Mark. His papacy lasted 40 years.",
     "The 117th Pope of Alexandria and Patriarch of the See of St. Mark."),
    ("John was a monk. He taught in Damascus c. AD 675; he died in 749.", "He taught in Damascus c. AD 675; he died in 749."),
    ('Ibsen was a playwright. He is often referred to as "the father of modern drama". He pioneered realism.',
     'He is often referred to as "the father of modern drama".'),
    ('Yukawa was a physicist who received the Nobel Prize "for his prediction of mesons." He taught at Kyoto.',
     'A physicist who received the Nobel Prize "for his prediction of mesons."'),
])
def test_contribution_ends_the_sentence_where_it_ends(paragraph, text):
    assert contribution(paragraph, "Monk" if "monk" in paragraph else "", None) == text


def test_contribution_passes_over_a_sentence_left_hanging():
    paragraph = "Ambrósio I was a mwenekongo of the Kingdom of Kongo who ruled from (March 1626 to March 7, 1631.)"
    assert contribution(paragraph, "Manikongo", "Ruler of the Kingdom of Kongo") == "Ruler of the Kingdom of Kongo."


def test_contribution_takes_fewer_new_words_when_asked():
    paragraph = "The Grand Principality of Vladimir was a Russian principality."
    assert contribution(paragraph, "Principality of Rostov-Suzdal", None) == ""
    assert contribution(paragraph, "Principality of Rostov-Suzdal", None, 1) == "A Russian principality."


@pytest.mark.parametrize("extract, paragraph", [
    ("Anton Chekhov (; Russian: Антон Чехов;\n29 January 1860 – 15 July 1904) was a playwright.\nHe was a physician.",
     "Anton Chekhov (; Russian: Антон Чехов; 29 January 1860 – 15 July 1904) was a playwright."),
    ("Note: names are in simplified characters.\nWu Yun was a general.\nHe died in 484 BC.", "Wu Yun was a general."),
    ("\nUgarit was a city.\nIt was found in 1928.", "Ugarit was a city."),
])
def test_first_paragraph(extract, paragraph):
    assert first_paragraph(extract) == paragraph


@pytest.mark.parametrize("description, dated", [
    ("Ancient Mesoamerican city", True), ("Principality of Romania from 1330 to 1862", True),
    ("Historical Iranian region in Azerbaijan", True), ("Pre-Roman civilization of Etruria (9th–1st century BC)", True),
    ("Country in Northern Europe", False), ("Ceremonial county in England", False), ("Sovereign state", False),
])
def test_dated_descriptions(description, dated):
    assert bool(DATED.search(description)) == dated


def test_commons_credit_drops_hidden_copies():
    assert visible('Unknown author<span style="display: none;">Unknown author</span>') == "Unknown author"
    assert visible('<a href="x">Jane</a> &amp; Doe') == "Jane & Doe"


def test_contribution_skips_a_sentence_that_leans_on_the_one_before():
    paragraph = ("Edward I was King of England from 1272 to 1307. Concurrently, he was Lord of Ireland. "
                 "He conquered Wales and fought the Scots.")
    assert contribution(paragraph, "King of England from 1272 to 1307", None) == "He conquered Wales and fought the Scots."


def test_commons_credit_drops_footnote_marks():
    assert visible("Unknown author, restored by Khánh ([1])") == "Unknown author, restored by Khánh"
    assert visible("unknown; according to [1] and [2] a portrait") == "unknown"
    assert visible("derivative work: Snitty (<a>talk</a>) Maome.jpg: Unknown") == "derivative work: Snitty Unknown"
    assert visible("A.Skromnitsky (<a>A.Skromnitsky</a> (<a>talk</a>))") == "A.Skromnitsky"
    assert visible("User:-hax0r") == "-hax0r"
    assert visible("Creator:Wojciech Stefanowski") == "Wojciech Stefanowski"


@pytest.mark.parametrize("paragraph", [
    "Muhammad Shah was Mughal emperor from 1719 to 1748. He was son of Khujista Akhtar.",
    "Euric was King of the Visigoths from 466 to 484. Sometimes he is called Euric II.",
    "Umar Shaikh Mirza was ruler of Fergana from 1469 to 1494. His son was Babur.",
    "Igor was Prince of Kiev from 912 to 945. According to the Chronicle, Rurik was succeeded by Oleg.",
    "Orodes was King of Parthia from 57 to 38 BC. The two brothers quickly fell out.",
])
def test_contribution_skips_parentage_and_name_notes(paragraph):
    assert contribution(paragraph, paragraph.split(". ")[0].split(" was ")[1], None) == ""


def test_contribution_keeps_a_sentence_that_only_opens_with_family():
    paragraph = ("Abbas was Shah of Iran from 1588 to 1629. The third son of Shah Mohammad, he is considered the greatest "
                 "ruler of the Safavid dynasty.")
    assert contribution(paragraph, "Shah of Iran from 1588 to 1629", None).startswith("The third son")


def test_label_falls_back_to_the_multilingual_one():
    assert Entity({"id": "Q189145", "labels": {"mul": {"value": "Antonio López de Santa Anna"}}}).label == "Antonio López de Santa Anna"
    assert Entity({"id": "Q484", "labels": {"en": {"value": "Babur"}, "mul": {"value": "Bābur"}}}).label == "Babur"


def test_commons_credit_drops_file_names():
    assert visible("Honourable Bertrand Russell.jpg: Photographer not identified") == "Photographer not identified"
    assert visible("A_b.jpg: *A.jpg: Alex Ehrenzweig derivative work: RedAppleJack") == "Alex Ehrenzweig derivative work: RedAppleJack"


ULM, NEAR_ULM, MUMBAI, SHIVNERI = (9.99, 48.4), (10.2, 48.6), (72.88, 19.08), (73.86, 19.2)


@pytest.mark.parametrize("places, years, birth", [
    ([ULM, ULM, ULM], [1879, 1879, 1879], Birth(ULM, 1879)),
    ([None, NEAR_ULM, ULM], [None, 1879, 1880], Birth(NEAR_ULM, 1879)),
    ([MUMBAI, SHIVNERI, SHIVNERI], [1630, 1630, 1630], None),
    ([SHIVNERI, SHIVNERI, None], [1630, None, 1631], Birth(SHIVNERI, 1630)),
    ([MUMBAI, SHIVNERI, None], [1630, 1630, 1630], None),
    ([ULM, None, None], [1879, 1879, None], None),
    ([ULM, ULM, None], [1500, None, 1440], None),
])
def test_a_birth_needs_two_sources_on_place_and_year(places, years, birth):
    assert agreed_birth(places, years) == birth


@pytest.mark.parametrize("names, country, shown", [
    (["Ottoman Empire"], "Israel", ["Ottoman Empire (modern-day Israel)"]),
    (["Kingdom of Romania"], "Romania", ["Kingdom of Romania"]),
    (["Qing Dynasty", "Republic of China"], "China", ["Qing Dynasty (modern-day China)", "Republic of China"]),
    (["Han Dynasty"], "", ["Han Dynasty"]),
    ([], "China", []),
])
def test_modern_country_follows_the_first_state(names, country, shown):
    assert with_modern_country(names, {"modern_country": country}) == shown


@pytest.mark.parametrize("value, earliest", [(300, 201), (1240, 1201), (901, 901), (-600, -600), (-550, -600), (-1, -100)])
def test_a_century_is_the_one_holding_the_year(value, earliest):
    assert Year(value, YEAR_PRECISION - 2, False).earliest == earliest


@pytest.mark.parametrize("names, opening, sentence, hidden", [
    (["Alan Turing"], "Alan Mathison Turing (1912 – 1954) was an English mathematician.",
     "Alan Mathison Turing was widely regarded as the father of theoretical computer science",
     "This person was widely regarded as the father of theoretical computer science"),
    (["Wisława Szymborska"], "Maria Wisława Anna Szymborska (1923 – 2012) was a Polish poet.",
     "In Poland, Szymborska's books have reached sales rivaling prose authors",
     "In Poland, this person's books have reached sales rivaling prose authors"),
    (["Zhao Tuo"], "Zhao Tuo, also known as Emperor Wu and as Triệu Đà, was a Chinese general.",
     "Triệu Đà founded the kingdom of Nanyue", "This person founded the kingdom of Nanyue"),
    (["Catherine I of Russia"], "Catherine I (1684 – 1727) was Empress of Russia.",
     "Catherine I was Empress of Russia from 1725", "This person was Empress of Russia from 1725"),
    (["Emperor Wen of Chen"], "Emperor Wen of Chen (522–566) was an emperor.",
     "He was succeeded by his brother, the Emperor Xuan", "He was succeeded by his brother, the Emperor Xuan"),
    (["Francis II Rákóczi"], "Francis II Rákóczi (1676 – 1735) was a Hungarian nobleman.",
     "He led Rákóczi's War of Independence against the Habsburgs", None),
    (["Zoroaster"], "Zoroaster, also known as Zarathustra, was an Iranian reformer.",
     "Zoroaster composed hymns that cover core Zoroastrian beliefs", None),
    (["Tokugawa Yoshinobu"], "Prince Tokugawa Yoshinobu (1837 – 1913) was a Japanese samurai.",
     "Yoshinobu was the 15th and last shōgun of the Tokugawa shogunate", None),
    (["Anna Leopoldovna", "Elisabeth Katharina Christine"], "Anna Leopoldovna (1718 – 1746) was regent of Russia.",
     "She worked with Field Marshal Burkhard Christoph von Münnich to overthrow him", None),
    (["Sun Quan"], "Sun Quan (182 – 252) was the founder of Eastern Wu.",
     "He was succeeded by his son, Sun Liang", None),
    (["Mohammad Daoud Khan"], "Mohammad Daoud Khan (1909 – 1978) was an Afghan politician.",
     "Having failed to persuade the King, Khan overthrew the monarchy in a coup", None),
    (["Olav V"], "Olav V (1903 – 1991) was King of Norway.",
     "King Olav's leadership made him a symbol of Norwegian independence",
     "This person's leadership made him a symbol of Norwegian independence"),
])
def test_a_legacy_line_hides_every_part_of_the_name(names, opening, sentence, hidden):
    assert hide(sentence, name_forms(names, opening)) == hidden


def test_legacy_sections_are_found_by_heading():
    text = "Intro.\n\n== Early life ==\nBorn in Ulm.\n\n== Legacy ==\nHe founded modern physics.\n\n== Notes ==\nx"
    assert legacy_sections(text).strip() == "He founded modern physics."


class FixedScores(LegacyScores):
    def __init__(self, scores: dict[str, int]):
        super().__init__(pathlib.Path("unused"))
        self.fixed = scores

    def scores(self, subject, name, role, sentences):
        return [self.fixed.get(sentence, 0) for sentence in sentences]


def test_a_sentence_ends_after_a_single_quote():
    assert sentences("The caliphs were 'rightly guided'. Endowed with piety, their era is a golden age.") == [
        "The caliphs were 'rightly guided'", "Endowed with piety, their era is a golden age."]


def test_a_legacy_line_that_repeats_the_summary_is_not_used():
    summary = "The largest contiguous empire in history."
    assert repeats("The Mongol Empire was the largest contiguous empire in history", summary)
    assert not repeats("It connected East and West in a Pax Mongolica, allowing the exchange of trade and ideas", summary)


def test_a_civilization_legacy_line_stands_on_its_own_and_scores_seven():
    text = ("The empire was the largest in history. However, it spread its laws across the region. "
            "It spread its language, laws, and customs. Its rulers fought many wars with neighbours.")
    scores = FixedScores({"The empire was the largest in history": 9, "However, it spread its laws across the region": 9,
                          "It spread its language, laws, and customs": 7, "Its rulers fought many wars with neighbours.": 6})
    lines = good_lines(scores, CIVILIZATION, "Mali Empire", "civilization", text, summary="The largest empire in history.")
    assert list(lines) == ["It spread its language, laws, and customs."]
    lines = good_lines(scores, CIVILIZATION, "Mali Empire", "civilization", "Its rulers fought many wars with neighbours.")
    assert list(lines) == []


def test_every_civilization_card_back_shows_the_legacy_line():
    assert all("{{Legacy}}" in template.back for template in CIVILIZATION_TEMPLATES)


def test_a_civilization_legacy_line_does_not_point_back_and_is_tidied():
    text = ("From then on the latter also shaped the language of the court. "
            "It was the legal predecessor of  the present-day Federal Republic of Germany,")
    scores = FixedScores({"From then on the latter also shaped the language of the court": 9,
                          "It was the legal predecessor of  the present-day Federal Republic of Germany,": 8})
    assert next(good_lines(scores, CIVILIZATION, "Brandenburg", "civilization", text), "") \
        == "It was the legal predecessor of the present-day Federal Republic of Germany."


def test_a_civilization_legacy_line_names_its_subject_and_gives_no_pronunciation():
    text = ("He made a famous pilgrimage to Mecca that caused inflation in Egypt. "
            "Toltec culture emanating from Tōllān [ˈtoːlːãːn̥] was the epitome of civilization. "
            "At its peak, Mali was the largest empire in West Africa.")
    scores = FixedScores({"He made a famous pilgrimage to Mecca that caused inflation in Egypt": 9,
                          "Toltec culture emanating from Tōllān [ˈtoːlːãːn̥] was the epitome of civilization": 9,
                          "At its peak, Mali was the largest empire in West Africa.": 8})
    assert list(good_lines(scores, CIVILIZATION, "Mali Empire", "civilization", text)) == [
        "At its peak, Mali was the largest empire in West Africa."]


def test_a_legacy_erratum_is_matched_when_its_line_is_passed_over():
    erratum = Erratum("legacy", "North Africa hosted many writers.", "About Roman North Africa.", civilization="Vandal Kingdom")
    other = Erratum("after", "Byzantine Empire", "A list answer.", civilization="Vandal Kingdom")
    assert legacy_rejections([erratum, other]) == {("Vandal Kingdom", "North Africa hosted many writers")}
    assert passed_legacy([erratum, other], {("Vandal Kingdom", "North Africa hosted many writers")}) == [erratum]
    assert passed_legacy([erratum], set()) == []


def test_a_legacy_line_may_hold_thin_spaces():
    text = "The world's earliest known texts come from Uruk and date to c. 3350 BC."
    scores = FixedScores({text: 9})
    assert list(good_lines(scores, CIVILIZATION, "Sumer", "civilization", text)) == [text]


def test_only_sentences_a_footnote_covers_are_cited():
    page = ('<section><p>The kingdom built canals. It traded salt widely.<sup class="mw-ref reference">[1]</sup> '
            'Its coins were famous across the region.</p></section>'
            '<section><p>Its scholars wrote on astronomy for centuries.<sup class="Inline-Template">[citation needed]</sup> '
            'The court spoke Persian and wrote in Arabic script.<sup class="reference">[2]</sup></p></section>'
            '<section><div class="box-Unreferenced">x</div><p>Its laws were copied by its neighbours for ages.'
            '<sup class="reference">[3]</sup></p></section>')
    assert cited(page) == ["The kingdom built canals.", "It traded salt widely.",
                           "The court spoke Persian and wrote in Arabic script."]


@pytest.mark.parametrize("names, realms, sentence, hidden", [
    (["Fahd of Saudi Arabia"], ("Kingdom of Saudi Arabia",), "He introduced the Basic Law of Saudi Arabia in 1992",
     "He introduced the Basic Law of Saudi Arabia in 1992"),
    (["Taejo of Joseon"], ("Joseon",), "Taejo was the first monarch of Joseon", "This person was the first monarch of Joseon"),
    (["Joan of Arc"], ("Kingdom of France",), "Joan of Arc lifted the siege of Orléans", "This person lifted the siege of Orléans"),
    (["Commodus"], (), "His reign is commonly thought to end the Pax Romana", "His reign is commonly thought to end the Pax Romana"),
    (["Garcia II", "Garcia Afonso for short"], (), "He is Kongo's greatest king for his piety",
     "He is Kongo's greatest king for his piety"),
    (["Anders Celsius"], (), "The scale was later renamed Celsius in his honour", None),
    (["Louis Pasteur"], (), "Pasteur discovered vaccination and pasteurization", None),
    (["Daniel Gabriel Fahrenheit"], (), "Observers could use their own Fahrenheit thermometers", None),
    (["Khafre"], (), "His pyramid at Giza, the Pyramid of Khafre, is the second largest", None),
    (["Ahmad ibn Ibrahim al-Ghazi"], (), "Imam Ahmed won nearly all his battles against the Ethiopians", None),
    (["Ahmad ibn Ibrahim al-Ghazi"], (), "Imam Ahmed's conquests reached the borders of Funj", None),
    (["Ibn al-Jazzar"], (), "Ibn al-Jazzar, was a physician famous for his writings", "This person was a physician famous for his writings"),
    (["An Dương Vương"], (), "An Dương Vương was the founding king of Âu Lạc", "This person was the founding king of Âu Lạc"),
    (["Sergio Osmeña", "Sergio Osmeña Sr."], (), "Sergio Osmeña Sr. was a Filipino lawyer", "This person was a Filipino lawyer"),
])
def test_a_legacy_line_hides_only_the_name(names, realms, sentence, hidden):
    assert hide(sentence, name_forms(names, "", realms)) == hidden


def test_a_broken_sentence_is_not_a_legacy_line_and_a_closing_quote_gets_its_full_stop():
    text = 'Writers celebrated him alongside Ranna. heralded the classical era of literature. It became "the empire on which the sun never sets"'
    scores = FixedScores({'Writers celebrated him alongside Ranna. heralded the classical era of literature': 9,
                          'It became "the empire on which the sun never sets"': 8})
    assert list(good_lines(scores, CIVILIZATION, "Spain", "civilization", text)) == [
        'It became "the empire on which the sun never sets".']


def test_a_rejected_legacy_line_stays_rejected_when_its_full_stop_changes():
    erratum = Erratum("legacy", 'It was declared "one and indivisible"', "A single event.", civilization="French First Republic")
    assert ("French First Republic", 'It was declared "one and indivisible"') in legacy_rejections([erratum])
    assert passed_legacy([erratum], {("French First Republic", 'It was declared "one and indivisible"')}) == [erratum]


@pytest.mark.parametrize("names, realms, sentence, hidden", [
    (["Abd al-Malik ibn Marwan"], (), "Abd al-Malik's reforms introduced a single Islamic currency",
     "This person's reforms introduced a single Islamic currency"),
    (["Johannes Gensfleisch zur Laden zum Gutenberg"], (), "Johannes Gensfleisch zur Laden zum Gutenberg invented the printing press",
     "This person invented the printing press"),
    (["Juan Ramón Jiménez"], (), "One of Jiménez's most important contributions was pure poetry",
     "One of this person's most important contributions was pure poetry"),
    (["Ganga Zumba"], (), "Ganga Zumba led Quilombo dos Palmares, or Angola Janga", "This person led Quilombo dos Palmares, or Angola Janga"),
    (["John, King of England"], ("Kingdom of England",), "John lost Normandy to King Philip II of France",
     "This person lost Normandy to King Philip II of France"),
])
def test_a_legacy_line_keeps_particles_and_other_names(names, realms, sentence, hidden):
    assert hide(sentence, name_forms(names, "", realms)) == hidden


@pytest.mark.parametrize("names, opening, sentence, hidden", [
    (["Tiberius"], "Tiberius Julius Caesar Augustus (42 BC – AD 37) was Roman emperor.",
     "Augustus adopted him as his heir in AD 4", None),
    (["Hasan ibn Ali"], "", "Hasan succeeded his father Ali as caliph", None),
    (["Alan Turing"], "Alan Mathison Turing (1912 – 1954) was an English mathematician.",
     "Alan Mathison Turing was an English mathematician", "This person was an English mathematician"),
    (["Ibn Hajar al-Haytami"], "", "Ibn Hajar al-Haytami was a leading Shafi'i jurist", "This person was a leading Shafi'i jurist"),
    (["Joan of Arc"], "", "Joan of Arc lifted the siege of Orléans", "This person lifted the siege of Orléans"),
    (["Parameswara"], "", "Parameswara founded the port of Malacca", "This person founded the port of Malacca"),
    (["Edward III of England"], "", "He was crowned after his father, Edward II, was deposed", None),
    (["Edward III of England"], "", "Edward III made England a military power", "This person made England a military power"),
    (["Mary, mother of Jesus"], "", "Mary is venerated in Christianity", "This person is venerated in Christianity"),
    (["Mary, mother of Jesus"], "", "She is said to have conceived Jesus by the Holy Spirit",
     "She is said to have conceived Jesus by the Holy Spirit"),
    (["Fibonacci"], "", "He is known for the Fibonacci numbers in Western mathematics", None),
    (["Catherine of Alexandria"], "", "The Catherine wheel is named after her martyrdom", None),
    (["Bernard Freyberg"], "", "He became the youngest general in the British Army", "He became the youngest general in the British Army"),
    (["Al Capone"], "", "He was nicknamed Scarface after a fight in a bar", None),
    (["Lakshmibai, Rani of Jhansi"], "", "Lakshmibai, Rani of Jhansi was one of the leading figures of the rebellion",
     "This person was one of the leading figures of the rebellion"),
    (["Nizam-ul-Mulk, Asaf Jah I"], "", "Political intrigues compelled Asaf Jah to rebel against the emperor", None),
    (["Tamar of Georgia"], "", "Tamar the Great reigned as the Queen of Georgia", "This person reigned as the Queen of Georgia"),
    (["Ganga Zumba"], "Nganga Nzumba (c. 1630 – 1678) was the first leader of Palmares.",
     "Nganga Nzumba was the first leader of the runaway slave settlement", "This person was the first leader of the runaway slave settlement"),
    (["Jin Yong"], "Louis Cha Leung-yung (1924 – 2018), better known by his pen name Jin Yong, was a novelist.",
     "Cha authored 15 novels between 1955 and 1972", None),
    (["Liu Bei"], "", "His courtesy name was Xuande and he founded Shu Han", None),
    (["Ken Arok"], "", "Ken Arok, Rajasa, was the founder of Singhasari", None),
    (["Catherine I of Russia"], "Catherine I (1684 – 1727) was Empress of Russia.",
     "Catherine was the first woman to rule Imperial Russia, including her daughter Elizabeth and granddaughter-in-law "
     "Catherine the Great", None),
    (["Ibn al-Haytham"], "Ḥasan Ibn al-Haytham (Latinized as Alhazen; c. 965 – 1040) was an Arab mathematician.",
     "Alhazen was the first to explain that vision occurs when light reflects into the eyes",
     "This person was the first to explain that vision occurs when light reflects into the eyes"),
])
def test_a_legacy_line_hides_only_the_persons_own_name(names, opening, sentence, hidden):
    assert hide(sentence, name_forms(names, opening), name_forms(names, "")) == hidden


@pytest.mark.parametrize("sentence", [
    "Three months later he was named president of the republic",
    "In this position he reorganised the army of the republic",
    "Henceforth the colony was ruled from London by the crown",
    "Shortly afterwards the new king was crowned in Uppsala",
    "The constitution was suspended by the emperor in that year",
])
def test_a_figure_legacy_line_stands_on_its_own(sentence):
    assert not list(good_lines(FixedScores({sentence: 10}), FIGURE, "Abeid Karume", "president", sentence))


def test_a_name_particle_at_the_start_of_a_sentence_goes_with_the_name():
    assert hide("Del Pilar founded the Diariong Tagalog newspaper", name_forms(["Marcelo H. del Pilar"], "")) \
        == "This person founded the Diariong Tagalog newspaper"


def test_a_figure_erratum_drops_a_wrong_state_known_for_line_or_life():
    from types import SimpleNamespace
    from total_history.errata import apply_figure_errata
    figure = SimpleNamespace(name="Kimpa Vita", civilizations=["Kathmandu", "Kingdom of Kongo"], related=[],
                             contribution="A fragment.", life="c. 55 BC – c. AD 12")
    errata = [Erratum("civilizations", "Kathmandu", "No tie.", figure="Kimpa Vita"),
              Erratum("contribution", "A fragment.", "Says nothing.", figure="Kimpa Vita"),
              Erratum("life", "c. 55 BC – c. AD 12", "No source gives dates.", figure="Kimpa Vita"),
              Erratum("civilizations", "Mali Empire", "No tie.", figure="Kimpa Vita")]
    assert apply_figure_errata([figure], errata) == errata[:3]
    assert figure.civilizations == ["Kingdom of Kongo"] and figure.contribution == "" and figure.life == ""


def test_an_umbrella_part_counts_only_while_it_was_part_of_it():
    from total_history.figures import Part
    algeria = Part("Q262", None, 1962)
    assert algeria.within((1925, 1980)) == (1925, 1961)
    assert algeria.within((1965, 1980)) is None
    assert Part("Q1", 1871, None).within((1850, 1900)) == (1871, 1900)


@pytest.mark.parametrize("sentence", [
    "He is the best archaeologically-attested ruler of this period, credited with bringing prosperity to his realm",
    "He invented Onitsha-style chapbooks during this time",
    "The memory of his presidency remains prominent due to the progress that Haiti experienced during this era",
    "He ranks high in the military history of that time as the most persistent opponent of the Ottoman Empire",
])
def test_a_figure_legacy_line_does_not_point_at_a_period_it_leaves_unnamed(sentence):
    assert not list(good_lines(FixedScores({sentence: 10}), FIGURE, "Abeid Karume", "president", sentence))


@pytest.mark.parametrize("sentence", [
    "He is best known for the polemic that recounts the sub-Roman history of Britain, the only source for history of this period",
    "He renounced Islam and led the resistance, this time directed against the Ottomans",
])
def test_a_figure_legacy_line_may_name_its_period_or_say_this_time(sentence):
    assert list(good_lines(FixedScores({sentence: 10}), FIGURE, "Abeid Karume", "president", sentence))


def test_a_figure_legacy_line_is_about_the_person_not_a_group():
    group = ("They embarked on an epic journey, covering nearly 2,000 miles, through the American interior, "
             "becoming the first Europeans and African to enter the American West")
    scores = FixedScores({group: 10})
    assert not list(good_lines(scores, FIGURE, "Esteban de Dorantes", "explorer", group))
    assert list(good_lines(scores, CIVILIZATION, "Lydia", "civilization", group))


def steps(*spans: tuple[int, int]) -> pd.DataFrame:
    return pd.DataFrame(spans, columns=["FromYear", "ToYear"])


@pytest.mark.parametrize("held, year, years", [
    (steps((1898, 1941), (1945, 1946)), 1945, range(1898, 1947)),
    (steps((1898, 1902), (1906, 1909)), 1907, range(1906, 1910)),
    (steps((1941, 1941), (1942, 1945)), 1943, range(1941, 1946)),
    (steps((1941, 1945)), 1950, range(0)),
])
def test_holding_bridges_a_short_loss_of_a_lasting_hold(held, year, years):
    assert holding(held, year) == years


def test_an_unbridged_hold_ends_at_any_break():
    assert holding(steps((1516, 1918), (1922, 1922)), 1922, bridged=False) == range(1922, 1923)


@pytest.mark.parametrize("held, home, occupied", [
    (range(1941, 1946), range(1776, 2025), True),
    (range(1898, 1947), range(1776, 2025), False),
    (range(1848, 1853), range(1848, 1853), False),
    (range(1942, 1944), range(1940, 1944), False),
])
def test_occupation_is_a_short_hold_away_from_a_home_still_held(held, home, occupied):
    assert occupation(held, home) == occupied


def test_an_overseas_occupation_is_cut_from_the_map():
    home, greenland, philippines = box(-100, 35, -98, 37), box(-40, 70, -38, 72), box(122, 10, 124, 12)
    frame = gpd.GeoDataFrame({
        "Name": ["United States"] * 4 + ["Denmark"],
        "FromYear": [1898, 1941, 1945, 1946, 1941], "ToYear": [1940, 1944, 1945, 2024, 1945],
        "geometry": [MultiPolygon([home, philippines]), MultiPolygon([home, greenland]),
                     MultiPolygon([home, greenland, philippines]), MultiPolygon([home, philippines]),
                     box(10, 55, 12, 57)],
    }, crs="EPSG:4326")
    builder = object.__new__(PolityBuilder)
    builder.cliopatria = Cliopatria(frame, spikes=[])
    shapes = builder._shapes("United States", [], 1945, [{"location": [-99.0, 36.0]}], None)
    assert shapes[shapes.Name == "United States"].geometry.union_all().equals(MultiPolygon([home, philippines]))
    assert set(shapes.Name) == {"United States", "Denmark"}


def test_a_change_of_regime_is_an_event_not_a_neighbour():
    assert NEIGHBOUR_ROOTS[REGIME_CHANGE] == "event"
    assert not PolityBuilder._stateful({"period", "event"})
    assert PolityBuilder._stateful({"period"})
