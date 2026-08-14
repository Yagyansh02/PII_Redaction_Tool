"""Low-level DOCX traversal and replacement without rebuilding the document.

The implementation edits only XML text nodes inside a copy of the original ZIP
package, so images, styles, numbering, section breaks and relationships survive.
Both visible ``w:t`` nodes and field-code ``w:instrText`` nodes are included.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from lxml import etree


NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "xml": "http://www.w3.org/XML/1998/namespace",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "cp": "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
}
W_P = f"{{{NS['w']}}}p"
W_TC = f"{{{NS['w']}}}tc"
W_TR = f"{{{NS['w']}}}tr"
W_TBL = f"{{{NS['w']}}}tbl"
W_T = f"{{{NS['w']}}}t"
W_INSTR = f"{{{NS['w']}}}instrText"
W_TAB = f"{{{NS['w']}}}tab"
W_BR = f"{{{NS['w']}}}br"
W_CR = f"{{{NS['w']}}}cr"
VIRTUAL_TEXT = {W_TAB: "\t", W_BR: "\n", W_CR: "\n"}
XML_SPACE = f"{{{NS['xml']}}}space"
TEXT_PART_RE = re.compile(
    r"^word/(?:document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml$"
)
REDACTION_PROPERTY = "PiiRedactorVersion"
REDACTION_VERSION = "1.0"


def _nearest_ancestor(element: etree._Element, tag: str) -> etree._Element | None:
    parent = element.getparent()
    while parent is not None:
        if parent.tag == tag:
            return parent
        parent = parent.getparent()
    return None


def _nearest_paragraph(element: etree._Element) -> etree._Element | None:
    parent = element.getparent()
    while parent is not None:
        if parent.tag == W_P:
            return parent
        parent = parent.getparent()
    return None


def _own_text_nodes(paragraph: etree._Element) -> list[etree._Element]:
    nodes: list[etree._Element] = []
    for node in paragraph.iterdescendants():
        if node.tag in {W_T, W_INSTR, W_TAB, W_BR, W_CR} and _nearest_paragraph(node) is paragraph:
            nodes.append(node)
    return nodes


def _element_text(element: etree._Element) -> str:
    return "".join(
        VIRTUAL_TEXT.get(node.tag, node.text or "")
        for node in element.iter()
        if node.tag in {W_T, W_INSTR, W_TAB, W_BR, W_CR}
    )


@dataclass(slots=True)
class NodeSlice:
    node: etree._Element
    start: int
    end: int
    hard_boundary: bool = False


@dataclass(slots=True)
class TextRecord:
    record_id: str
    part_name: str
    paragraph: etree._Element
    text: str
    nodes: list[NodeSlice]
    cell_key: str | None = None
    table_headers: list[str] = field(default_factory=list)
    column_header: str = ""

    @property
    def metadata(self) -> dict[str, object]:
        return {
            "cell_key": self.cell_key,
            "table_headers": self.table_headers,
            "column_header": self.column_header,
        }

    def crosses_hard_boundary(self, start: int, end: int) -> bool:
        return any(
            node_slice.hard_boundary
            and node_slice.start < end
            and start < node_slice.end
            for node_slice in self.nodes
        )

    @property
    def current_text(self) -> str:
        """Text currently held by the XML paragraph after any replacements."""

        return _element_text(self.paragraph)


@dataclass(slots=True)
class BlockSegment:
    record: TextRecord
    start: int
    end: int


@dataclass(slots=True)
class TextBlock:
    block_id: str
    text: str
    segments: list[BlockSegment]
    metadata: dict[str, object] = field(default_factory=dict)

    def project(self, start: int, end: int) -> list[tuple[TextRecord, int, int]]:
        projected: list[tuple[TextRecord, int, int]] = []
        for segment in self.segments:
            overlap_start = max(start, segment.start)
            overlap_end = min(end, segment.end)
            if overlap_start >= overlap_end:
                continue
            local_start = overlap_start - segment.start
            local_end = overlap_end - segment.start
            projected.append((segment.record, local_start, local_end))
        return projected


@dataclass(slots=True)
class TableData:
    part_name: str
    table_id: str
    rows: list[list[str]]


@dataclass(frozen=True, slots=True)
class ImageReference:
    """An embedded raster image and the text surrounding its drawing."""

    part_name: str
    media_path: str
    paragraph_index: int
    nearby_text: str
    description: str = ""


class DocxPackage:
    def __init__(self, source: str | Path) -> None:
        self.source = Path(source)
        if not self.source.is_file():
            raise FileNotFoundError(self.source)
        self.entries: dict[str, bytes] = {}
        self.entry_order: list[str] = []
        self.xml_roots: dict[str, etree._Element] = {}
        parser = etree.XMLParser(resolve_entities=False, remove_blank_text=False, huge_tree=True)
        with zipfile.ZipFile(self.source) as archive:
            for info in archive.infolist():
                payload = archive.read(info.filename)
                self.entries[info.filename] = payload
                self.entry_order.append(info.filename)
                if TEXT_PART_RE.match(info.filename):
                    self.xml_roots[info.filename] = etree.fromstring(payload, parser)
        if "word/document.xml" not in self.xml_roots:
            raise ValueError(f"{self.source} is not a valid Word document")
        self._records: list[TextRecord] | None = None
        self._tables: list[TableData] | None = None

    @property
    def records(self) -> list[TextRecord]:
        if self._records is None:
            self._records = self._build_records()
        return self._records

    @property
    def tables(self) -> list[TableData]:
        if self._tables is None:
            self._tables = self._build_tables()
        return self._tables

    def _table_metadata(
        self, root: etree._Element
    ) -> tuple[dict[etree._Element, list[str]], dict[etree._Element, tuple[str, int]]]:
        headers: dict[etree._Element, list[str]] = {}
        cell_locations: dict[etree._Element, tuple[str, int]] = {}
        for table_index, table in enumerate(root.iter(W_TBL)):
            rows = [child for child in table if child.tag == W_TR]
            first_cells = [child for child in rows[0] if child.tag == W_TC] if rows else []
            table_headers = [re.sub(r"\s+", " ", _element_text(cell)).strip() for cell in first_cells]
            headers[table] = table_headers
            for row_index, row in enumerate(rows):
                cells = [child for child in row if child.tag == W_TC]
                for column_index, cell in enumerate(cells):
                    cell_locations[cell] = (f"t{table_index:04d}:r{row_index:04d}:c{column_index:03d}", column_index)
        return headers, cell_locations

    def _build_records(self) -> list[TextRecord]:
        records: list[TextRecord] = []
        for part_name in sorted(self.xml_roots, key=self._part_sort_key):
            root = self.xml_roots[part_name]
            table_headers, cell_locations = self._table_metadata(root)
            part_counter = 0
            for paragraph in root.iter(W_P):
                nodes = _own_text_nodes(paragraph)
                # Preserve the historical record-id contract: paragraphs that
                # contain only layout controls never formed text records.
                if not any(node.tag in {W_T, W_INSTR} for node in nodes):
                    continue
                slices: list[NodeSlice] = []
                pieces: list[str] = []
                offset = 0
                for node in nodes:
                    value = VIRTUAL_TEXT.get(node.tag, node.text or "")
                    is_boundary = node.tag in VIRTUAL_TEXT
                    slices.append(NodeSlice(node, offset, offset + len(value), is_boundary))
                    pieces.append(value)
                    offset += len(value)
                text = "".join(pieces)
                cell = _nearest_ancestor(paragraph, W_TC)
                table = _nearest_ancestor(paragraph, W_TBL)
                cell_key: str | None = None
                column_header = ""
                headers: list[str] = []
                if cell is not None and table is not None and cell in cell_locations:
                    location, column_index = cell_locations[cell]
                    cell_key = f"{part_name}:{location}"
                    headers = table_headers.get(table, [])
                    if column_index < len(headers):
                        column_header = headers[column_index]
                record_id = f"{part_name}:p{part_counter:06d}"
                records.append(
                    TextRecord(
                        record_id, part_name, paragraph, text, slices,
                        cell_key, headers, column_header,
                    )
                )
                part_counter += 1
        return records

    def _build_tables(self) -> list[TableData]:
        result: list[TableData] = []
        for part_name in sorted(self.xml_roots, key=self._part_sort_key):
            root = self.xml_roots[part_name]
            for table_index, table in enumerate(root.iter(W_TBL)):
                rows: list[list[str]] = []
                for row in (child for child in table if child.tag == W_TR):
                    rows.append([
                        re.sub(r"\s+", " ", _element_text(cell)).strip()
                        for cell in row if cell.tag == W_TC
                    ])
                result.append(TableData(part_name, f"{part_name}:t{table_index:04d}", rows))
        return result

    def image_references(self, context_paragraphs: int = 2) -> list[ImageReference]:
        """Return embedded-image targets with nearby Word text.

        Relationship targets are resolved per document part.  Nearby text is
        used only as a classification signal; image replacement never alters
        those paragraphs.
        """

        references: list[ImageReference] = []
        for part_name in sorted(self.xml_roots, key=self._part_sort_key):
            rels_name = posixpath.join(
                posixpath.dirname(part_name),
                "_rels",
                posixpath.basename(part_name) + ".rels",
            )
            rels_payload = self.entries.get(rels_name)
            if not rels_payload:
                continue
            parser = etree.XMLParser(resolve_entities=False, remove_blank_text=False)
            rels_root = etree.fromstring(rels_payload, parser)
            targets = {
                rel.get("Id", ""): rel.get("Target", "")
                for rel in rels_root
                if rel.get("TargetMode") != "External"
                and rel.get("Type", "").endswith("/image")
            }
            paragraphs = list(self.xml_roots[part_name].iter(W_P))
            paragraph_text = [_element_text(paragraph).strip() for paragraph in paragraphs]
            for paragraph_index, paragraph in enumerate(paragraphs):
                blips = paragraph.findall(".//a:blip", NS)
                if not blips:
                    continue
                left = max(0, paragraph_index - context_paragraphs)
                right = min(len(paragraphs), paragraph_index + context_paragraphs + 1)
                nearby = "\n".join(value for value in paragraph_text[left:right] if value)
                descriptions = [
                    value
                    for doc_property in paragraph.findall(".//wp:docPr", NS)
                    for value in (
                        doc_property.get("name"),
                        doc_property.get("descr"),
                        doc_property.get("title"),
                    )
                    if value
                ]
                description = " ".join(dict.fromkeys(descriptions))
                for blip in blips:
                    relationship_id = blip.get(f"{{{NS['r']}}}embed")
                    target = targets.get(relationship_id or "")
                    if not target:
                        continue
                    media_path = posixpath.normpath(
                        posixpath.join(posixpath.dirname(part_name), target)
                    )
                    if media_path not in self.entries:
                        continue
                    references.append(
                        ImageReference(
                            part_name,
                            media_path,
                            paragraph_index,
                            nearby,
                            description,
                        )
                    )
        return references

    def blocks(self) -> list[TextBlock]:
        by_cell: dict[str, list[TextRecord]] = {}
        for record in self.records:
            if record.cell_key:
                by_cell.setdefault(record.cell_key, []).append(record)
        blocks: list[TextBlock] = []
        for cell_key, records in by_cell.items():
            pieces: list[str] = []
            segments: list[BlockSegment] = []
            offset = 0
            for index, record in enumerate(records):
                if index:
                    pieces.append("\n")
                    offset += 1
                start = offset
                pieces.append(record.text)
                offset += len(record.text)
                segments.append(BlockSegment(record, start, offset))
            blocks.append(
                TextBlock(
                    cell_key,
                    "".join(pieces),
                    segments,
                    {
                        "table_headers": records[0].table_headers,
                        "column_header": records[0].column_header,
                        "cell_key": cell_key,
                    },
                )
            )
        return blocks

    def body_windows(self, size: int = 6) -> list[TextBlock]:
        """Overlapping windows for addresses split across ordinary paragraphs."""

        body = [
            record for record in self.records
            if record.part_name == "word/document.xml" and record.cell_key is None
        ]
        blocks: list[TextBlock] = []
        for window_start in range(len(body)):
            records = body[window_start : window_start + size]
            if len(records) < 2:
                break
            pieces: list[str] = []
            segments: list[BlockSegment] = []
            offset = 0
            for index, record in enumerate(records):
                if index:
                    pieces.append("\n")
                    offset += 1
                start = offset
                pieces.append(record.text)
                offset += len(record.text)
                segments.append(BlockSegment(record, start, offset))
            blocks.append(
                TextBlock(
                    f"body:{window_start:06d}",
                    "".join(pieces),
                    segments,
                    {"body_window": True},
                )
            )
        return blocks

    @staticmethod
    def apply_spans(record: TextRecord, replacements: Iterable[tuple[int, int, str]]) -> None:
        """Apply original-coordinate replacements right-to-left."""

        for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
            if start < 0 or end > len(record.text) or start >= end:
                raise ValueError(f"replacement [{start}, {end}) outside {record.record_id}")
            affected = [node_slice for node_slice in record.nodes if node_slice.start < end and start < node_slice.end]
            if not affected:
                raise ValueError(f"replacement [{start}, {end}) has no XML nodes in {record.record_id}")
            if any(node_slice.hard_boundary for node_slice in affected):
                raise ValueError(
                    f"replacement [{start}, {end}) crosses a tab/line break in {record.record_id}"
                )
            first, last = affected[0], affected[-1]
            first_value = first.node.text or ""
            first_local = max(0, start - first.start)
            if first is last:
                last_local = end - first.start
                DocxPackage._set_node_text(
                    first.node, first_value[:first_local] + replacement + first_value[last_local:]
                )
                continue
            DocxPackage._set_node_text(first.node, first_value[:first_local] + replacement)
            for node_slice in affected[1:-1]:
                DocxPackage._set_node_text(node_slice.node, "")
            last_value = last.node.text or ""
            last_local = end - last.start
            DocxPackage._set_node_text(last.node, last_value[last_local:])

    @staticmethod
    def _set_node_text(node: etree._Element, value: str) -> None:
        node.text = value
        if value[:1].isspace() or value[-1:].isspace():
            node.set(XML_SPACE, "preserve")
        else:
            node.attrib.pop(XML_SPACE, None)

    def is_redacted(self) -> bool:
        payload = self.entries.get("docProps/custom.xml")
        if not payload:
            return False
        parser = etree.XMLParser(resolve_entities=False, remove_blank_text=False)
        try:
            root = etree.fromstring(payload, parser)
        except etree.XMLSyntaxError:
            return False
        for prop in root.findall("cp:property", NS):
            if prop.get("name") == REDACTION_PROPERTY:
                return True
        return False

    def mark_redacted(self) -> None:
        self._write_custom_property()
        self._ensure_custom_relationship()
        self._ensure_custom_content_type()

    def _write_custom_property(self) -> None:
        name = "docProps/custom.xml"
        parser = etree.XMLParser(resolve_entities=False, remove_blank_text=False)
        if name in self.entries:
            root = etree.fromstring(self.entries[name], parser)
        else:
            root = etree.Element(f"{{{NS['cp']}}}Properties", nsmap={None: NS["cp"], "vt": NS["vt"]})
            self.entry_order.append(name)
        props = root.findall("cp:property", NS)
        prop = next((item for item in props if item.get("name") == REDACTION_PROPERTY), None)
        if prop is None:
            prop = etree.SubElement(root, f"{{{NS['cp']}}}property")
            prop.set("fmtid", "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}")
            prop.set("pid", str(max((int(item.get("pid", "1")) for item in props), default=1) + 1))
            prop.set("name", REDACTION_PROPERTY)
        for child in list(prop):
            prop.remove(child)
        value = etree.SubElement(prop, f"{{{NS['vt']}}}lpwstr")
        value.text = REDACTION_VERSION
        self.entries[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    def _ensure_custom_relationship(self) -> None:
        name = "_rels/.rels"
        parser = etree.XMLParser(resolve_entities=False, remove_blank_text=False)
        root = etree.fromstring(self.entries[name], parser)
        custom_type = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
        if any(rel.get("Type") == custom_type for rel in root):
            return
        ids = {rel.get("Id", "") for rel in root}
        index = 1
        while f"rId{index}" in ids:
            index += 1
        rel = etree.SubElement(root, f"{{{NS['rel']}}}Relationship")
        rel.set("Id", f"rId{index}")
        rel.set("Type", custom_type)
        rel.set("Target", "docProps/custom.xml")
        self.entries[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    def _ensure_custom_content_type(self) -> None:
        name = "[Content_Types].xml"
        parser = etree.XMLParser(resolve_entities=False, remove_blank_text=False)
        root = etree.fromstring(self.entries[name], parser)
        if any(item.get("PartName") == "/docProps/custom.xml" for item in root):
            return
        override = etree.SubElement(root, f"{{{NS['ct']}}}Override")
        override.set("PartName", "/docProps/custom.xml")
        override.set("ContentType", "application/vnd.openxmlformats-officedocument.custom-properties+xml")
        self.entries[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    def write(self, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        updated = dict(self.entries)
        for name, root in self.xml_roots.items():
            updated[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            seen: set[str] = set()
            for name in self.entry_order:
                if name in updated and name not in seen:
                    archive.writestr(name, updated[name])
                    seen.add(name)
            for name, payload in updated.items():
                if name not in seen:
                    archive.writestr(name, payload)
        return target

    @staticmethod
    def _part_sort_key(name: str) -> tuple[int, str]:
        if name == "word/document.xml":
            return (0, name)
        if "header" in name:
            return (1, name)
        if "footer" in name:
            return (2, name)
        return (3, name)
