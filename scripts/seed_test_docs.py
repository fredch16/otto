"""Write synthetic administrative documents into a consume folder (test stack only).

All names, numbers and IBANs are invented.
Usage: uv run --project mcp/paperless python scripts/seed_test_docs.py test/consume
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp" / "paperless" / "tests"))
from conftest import make_pdf  # noqa: E402

DOCUMENTS = {
    "mietvertrag-2025.pdf": [
        "Mietvertrag ueber Wohnraum\nVermieter: Hausverwaltung Neckarblick GmbH, Uferstraße 12, 74072 Heilbronn\n"
        "Mieter: Max Mustermann\nMietobjekt: 2-Zimmer-Wohnung, 3. OG links, Paulinenstraße 99, 74076 Heilbronn\n"
        "§ 1 Mietbeginn: Das Mietverhältnis beginnt am 01.03.2025 und läuft auf unbestimmte Zeit.\n"
        "§ 2 Miete: Die Kaltmiete beträgt 780,00 EUR monatlich, die Nebenkostenvorauszahlung 210,00 EUR.\n"
        "Die Miete ist bis zum dritten Werktag eines Monats auf das Konto DE12 5005 0000 1234 5678 90 zu zahlen.",
        "§ 3 Kaution: Der Mieter leistet eine Mietsicherheit von drei Kaltmieten (2.340,00 EUR).\n"
        "§ 4 Kündigung: Der Mieter kann mit einer Frist von drei Monaten zum Monatsende kündigen.\n"
        "Die Kündigungsfrist des Vermieters verlängert sich nach fünf und acht Jahren um jeweils drei Monate.\n"
        "§ 5 Schönheitsreparaturen trägt der Mieter nach Maßgabe der Anlage 2.\n"
        "§ 6 Haustiere: Kleintiere sind erlaubt; Hunde und Katzen nur mit schriftlicher Zustimmung.",
    ],
    "haftpflicht-police.pdf": [
        "Versicherungsschein Privathaftpflicht\nVersicherer: Muster Versicherung AG, Köln\n"
        "Versicherungsnummer: PH-2024-778899\nVersicherungsnehmer: Max Mustermann\n"
        "Versicherungsbeginn: 01.01.2024, Laufzeit ein Jahr, Verlängerung um jeweils ein Jahr.\n"
        "Jahresbeitrag: 64,20 EUR, fällig am 01.01. eines jeden Jahres per SEPA-Lastschrift.\n"
        "Kündigung: drei Monate vor Ablauf des Versicherungsjahres in Textform.\n"
        "Deckungssumme: 10 Mio. EUR pauschal für Personen-, Sach- und Vermögensschäden.",
    ],
    "stromrechnung-2026-10.pdf": [
        "Stadtwerke Muster - Rechnung Nr. 2026-10-55321\nKundennummer: 4711-0815\nZeitraum: 01.10.2026 bis 31.10.2026\n"
        "Verbrauch: 182 kWh, Arbeitspreis 0,31 EUR/kWh, Grundpreis 12,50 EUR\n"
        "Rechnungsbetrag: 68,92 EUR (brutto, inkl. 19 % MwSt.)\n"
        "Der Betrag wird am 15.11.2026 von Ihrem Konto DE98 6005 0101 0002 1234 56 abgebucht.",
    ],
    "steuerbescheid-2025.pdf": [
        "Finanzamt Musterstadt\nBescheid für 2025 über Einkommensteuer\nSteuernummer: 65432/10987\n"
        "Festgesetzt werden: Einkommensteuer 4.812,00 EUR, Solidaritätszuschlag 0,00 EUR.\n"
        "Bereits gezahlt: 5.430,00 EUR. Erstattung: 618,00 EUR.\n"
        "Rechtsbehelfsbelehrung: Gegen diesen Bescheid kann innerhalb eines Monats nach Bekanntgabe Einspruch eingelegt werden.",
    ],
    "gym-membership-en.pdf": [
        "FitClub Membership Agreement\nMember: Max Mustermann, membership no. FC-30917\n"
        "Start date: 1 February 2026. Minimum term: 12 months, then month to month.\n"
        "Monthly fee: 34.90 EUR, collected by direct debit on the 1st of each month.\n"
        "Cancellation: one month's notice to the end of the minimum term, in writing or by email.",
    ],
}


def main() -> None:
    target = Path(sys.argv[1])
    target.mkdir(parents=True, exist_ok=True)
    for name, pages in DOCUMENTS.items():
        (target / name).write_bytes(make_pdf(pages))
    print(f"Wrote {len(DOCUMENTS)} synthetic documents to {target}/")


if __name__ == "__main__":
    main()
