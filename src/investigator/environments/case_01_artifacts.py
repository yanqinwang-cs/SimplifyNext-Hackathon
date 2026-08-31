from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _column_number(reference: str) -> int:
    number = 0
    for character in (c for c in reference if c.isalpha()):
        number = number * 26 + ord(character.upper()) - ord("A") + 1
    return number


def render_xlsx(path: str | Path) -> str:
    with ZipFile(path) as workbook:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in workbook.namelist():
            root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
            shared = ["".join(node.text or "" for node in item.iter(MAIN_NS + "t")) for item in root.findall(MAIN_NS + "si")]
        workbook_root = ElementTree.fromstring(workbook.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
        targets = {r.attrib["Id"]: r.attrib["Target"] for r in rels}
        sheets = []
        for sheet in workbook_root.findall(f"{MAIN_NS}sheets/{MAIN_NS}sheet"):
            target = targets[sheet.attrib[REL_NS + "id"]].lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
            root = ElementTree.fromstring(workbook.read(target))
            rows = []
            for row in root.findall(f"{MAIN_NS}sheetData/{MAIN_NS}row"):
                cells = row.findall(MAIN_NS + "c")
                if not cells:
                    continue
                values = [""] * max(_column_number(c.attrib["r"]) for c in cells)
                for cell in cells:
                    value = cell.find(MAIN_NS + "v")
                    text = "" if value is None else value.text or ""
                    if cell.attrib.get("t") == "s":
                        text = shared[int(text)]
                    values[_column_number(cell.attrib["r"]) - 1] = text
                while values and not values[-1]:
                    values.pop()
                if values:
                    rows.append("\t".join(values))
            sheets.append(f"SHEET: {sheet.attrib['name']}\n" + "\n".join(rows))
        return "\n\n".join(sheets)


def render_artifact(path: str | Path) -> str:
    path = Path(path)
    return render_xlsx(path) if path.suffix.lower() == ".xlsx" else path.read_text(encoding="utf-8")
