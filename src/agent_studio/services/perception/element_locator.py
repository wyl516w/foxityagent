from __future__ import annotations

from agent_studio.core.models import ElementLookupResponse, ElementMatch, OcrResponse


class ElementLocator:
    def find_text(
        self,
        ocr_response: OcrResponse,
        query: str,
        case_sensitive: bool = False,
    ) -> ElementLookupResponse:
        if not ocr_response.ok:
            return ElementLookupResponse(
                ok=False,
                image_path=ocr_response.image_path,
                query=query,
                message=ocr_response.message,
            )

        needle = query if case_sensitive else query.lower()
        matches: list[ElementMatch] = []
        for line in ocr_response.lines:
            haystack = line.text if case_sensitive else line.text.lower()
            if needle in haystack:
                center_x, center_y = _bbox_center(line.bbox)
                matches.append(
                    ElementMatch(
                        text=line.text,
                        score=line.score,
                        bbox=line.bbox,
                        center_x=center_x,
                        center_y=center_y,
                    )
                )

        message = f"Found {len(matches)} matches for '{query}'."
        if not matches:
            message = f"No OCR text matched '{query}'."
        return ElementLookupResponse(
            ok=bool(matches),
            image_path=ocr_response.image_path,
            query=query,
            matches=matches,
            message=message,
        )


def _bbox_center(bbox: list[list[int]]) -> tuple[int, int]:
    if not bbox:
        return 0, 0
    xs = [point[0] for point in bbox]
    ys = [point[1] for point in bbox]
    return int(sum(xs) / len(xs)), int(sum(ys) / len(ys))

