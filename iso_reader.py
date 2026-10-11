"""Reads a photo of a plant piping iso with Claude and prepares it for the Iso Finder page.

Pure Python (Pillow + json). The model call is passed in as `ask`, so the same code runs against
the real API in the app and against a fake in tests.
"""
import base64
import io
import json

from PIL import Image, ImageOps

try:  # iPhone photos can arrive as HEIC
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    pass

# Step 1: find the drawing on the sheet and say how far the photo is turned.
P1 = 'This is a photo of one sheet of a plant piping isometric drawing (an "iso").\nReply with only JSON in this shape: {"turns":0,"box":{"x0":0,"y0":0,"x1":1000,"y1":1000}}\n"turns" is how many 90-degree CLOCKWISE turns the photo needs so the printed text reads upright, left to right (0, 1, 2 or 3).\n"box" is the rectangle that tightly contains the isometric pipe drawing itself, including its coordinate notes (CONT ON, CONN TO, EL, E, N) and dimension text. Leave out the material list, notes tables and title block. Give it in thousandths of the photo width and height, measured from the top-left corner of the photo as it is shown now (before any turning).'

# Step 2: trace the pipe, its lengths and its printed coordinates. Image 1 is the whole drawing
# (to place points); the tiles are zoomed copies (to read the small numbers).
def p2(cols, rows):
    t = cols * rows
    if t:
        head = (
            "These %d images show the isometric pipe drawing from a plant piping iso, already upright. "
            "Image 1 is the whole drawing. Images 2 to %d are zoomed-in tiles of the same drawing, %d across and %d down, "
            "in reading order (left to right, then top to bottom), and neighbouring tiles overlap by about 12%%. "
            "Use Image 1 to place points. Use the tiles to read every printed number exactly, because the print is tiny in Image 1."
            % (t + 1, t + 1, cols, rows)
        )
    else:
        head = "This image is a close-up of the isometric pipe drawing from a plant piping iso, already upright."
    return "\n".join(
        [
            head,
            'Trace the pipe as a list of points. A point is where the pipe ends, turns at an elbow, or branches at a tee. Number the points from 1 in order along the pipe. Start at an end that has printed coordinates (a note such as "CONT ON" or "CONN TO" with E, N and EL values) and finish at the other end. Add each branch pipe after the main pipe.',
            "Reply with only JSON in this shape:",
            '{"north":"UL","points":[{"id":1,"x":500,"y":300,"from":null,"go":null,"length":null,"E":null,"N":null,"EL":null}]}',
            "- x, y: where the point sits on the pipe line, in thousandths of the whole drawing's width and height (Image 1) from its top-left corner. Use the centre of an elbow or the end of the pipe. Put each point exactly on the pipe line.",
            "- from: the id of the point this run of pipe starts from. null for point 1, and null for the start of a separate line.",
            '- go: which way this run travels on the page, from its "from" point to this point: "UP", "DOWN", "UL", "UR", "DL" or "DR" (up-left, up-right, down-left, down-right). Iso pipe only runs straight up and down, or along the two slanted axes, so every run is one of these six. null for the first point of a line.',
            '- length: the dimension printed for the run from "from" to this point, copied exactly as printed (for example 5\'7", 2 1/2", 1\'1 7/8"). Only when that dimension measures that whole run. Otherwise null. Never guess.',
            '- E, N, EL: the coordinates printed for this exact point, copied exactly as printed with the feet and inch marks (example 12490\'7.7/8"). null when none are printed for it. If only EL is printed, give only EL.',
            '- north: which way the N arrow points on this image: "UL" up-left, "UR" up-right, "DR" down-right, "DL" down-left, or null if no N arrow is visible.',
            "Dimension text runs along its dimension line, so it can be slanted: read it at its angle. Look at each number before you copy it, and keep fractions exactly as printed (7/8 stays 7/8). If a digit is unclear, use null instead of guessing.",
            "Never invent numbers. Use null when unsure.",
        ]
    )


def p4(cols, rows):
    t = cols * rows
    if t:
        head = (
            "These %d images show the isometric pipe drawing from a plant piping iso, already upright. Image 1 is the whole drawing. "
            "Images 2 to %d are zoomed-in tiles of the same drawing, %d across and %d down, in reading order, overlapping by about 12%%. "
            "Use the tiles to read every number exactly. Use Image 1 for positions." % (t + 1, t + 1, cols, rows)
        )
    else:
        head = "This image is a close-up of the isometric pipe drawing from a plant piping iso, already upright."
    return "\n".join(
        [
            head,
            'Find every printed coordinate note on the drawing. These are notes at the ends of a pipe, such as "CONT ON", "CONN TO" or a tie-in, that give E, N and EL values. Also include E, N or EL values printed on their own beside a pipe end. Do not include lengths or dimensions, the title block, or line numbers.',
            "Reply with only JSON in this shape:",
            '{"notes":[{"label":"CONT ON","x":500,"y":300,"E":null,"N":null,"EL":null}]}',
            "- x, y: where the pipe end that this note points to sits, in thousandths of the whole drawing width and height (Image 1), from its top-left corner. If you cannot tell, give where the note text is.",
            '- E, N, EL: copy exactly as printed with the feet and inch marks (example 12490\'7.7/8"). Keep fractions as printed. null for a value that is not printed. A note is often printed twice with the same values: list it once.',
            "Text can be slanted or upside down: read it at its angle. Read every digit one at a time. If a digit is unclear, use null instead of guessing. Never invent numbers.",
        ]
    )


P6 = "\n".join(
    [
        'This is a photo of one whole sheet of a plant piping isometric drawing (an "iso"), already upright.',
        "Find the rectangles that hold printed tables: the material lists (fabrication and erection materials), the cut pipe length or spool table, the weld list, and the title block.",
        'Reply with only JSON in this shape: {"tables":[{"name":"materials","x0":0,"y0":0,"x1":300,"y1":400}]}',
        "name is one of: materials, cut, welds, title. Give each rectangle in thousandths of the photo width and height from its top-left corner. Make each rectangle tight but include the table header row and every row. Use one rectangle per table. Do not include the drawing of the pipe itself. Leave out tables you cannot see.",
    ]
)
KINDLIST = "pipe, elbow (90 or 45 degree), tee, reducer, fitting (couplings, caps, plugs, branch connections), valve, flange, gasket, bolt (bolts, studs and nuts), instrument, support, weld, cut, other"
SHAPE = '{"line":null,"items":[{"kind":"elbow","ref":"2","desc":"90 DEG ELBOW A105N CL.6000 SW","what":"90-degree elbow","size":"2\\"","qty":"4"}]}'
WHAT = '- what: a plain-English name a beginner understands, 2 to 6 words (for example "90-degree elbow", "tee", "gate valve", "flange", "spiral-wound gasket", "bolts and nuts", "pipe shoe support").'


def p5(n, focused):
    if focused:
        head = "These %d images are close-ups of the tables printed on one sheet of a plant piping isometric drawing: material lists, cut pipe lengths, and the weld list." % n
    else:
        head = (
            'These %d images show one whole sheet of a plant piping isometric drawing (an "iso"). Image 1 is the whole sheet and the others are zoomed tiles. '
            "Find the tables of text (material lists, cut pipe lengths, weld list) and read them." % n
        )
    return "\n".join(
        [
            head,
            "Read every row of every table. Copy from the sheet. Do not invent anything. One entry per row. Weld list: one entry per weld tag (for example F1, S1). Cut pipe list: one entry per piece.",
            "Reply with only JSON in this shape:",
            SHAPE,
            "- line: the line or drawing number from the title block, or null.",
            "- kind: one of " + KINDLIST + ".",
            "- ref: the item number, tag, weld number or piece number as printed. desc: the description as printed, shortened to 80 characters. size and qty: as printed (qty for pipe is a length), or null.",
            WHAT,
            "Text can be small or slanted: read each row at its angle, one cell at a time. If a cell is unclear use null for that cell. If a table is unreadable, skip it. Never guess.",
        ]
    )


def p7(cols, rows, ctx):
    t = cols * rows
    if t:
        head = (
            "These %d images show the isometric pipe drawing from a plant piping iso, already upright. Image 1 is the whole drawing. "
            "Images 2 to %d are zoomed-in tiles of it, %d across and %d down, in reading order, overlapping by about 12%%. Use the tiles to read small print." % (t + 1, t + 1, cols, rows)
        )
    else:
        head = "This image is a close-up of the isometric pipe drawing from a plant piping iso, already upright."
    lines = [
        head,
        "A person will walk this pipe and check off what is on it. List what is drawn on it:",
        '- kind "mark": every circled number along the pipe, in the order you meet them walking from one end of the line to the other. If the circled number matches a number in the NO column of the material list below, copy that item\'s description into desc and give what. Otherwise leave desc null.',
        '- kind "tag": every diamond or boxed tag such as J1, J2 or S18. desc: any text beside it.',
        '- kind "support": boxed support tags such as PS-001 or SP1. desc: any text beside it.',
        "- kind valve, tee or elbow: any valve, tee or elbow that is drawn and labeled on the pipe, if it is not already a circled mark.",
    ]
    if ctx:
        lines.append("Material list already read (NO: description):\n" + ctx)
    lines += [
        "Reply with only JSON in this shape:",
        '{"items":[{"kind":"mark","ref":"12","desc":"90 DEG ELBOW A105N CL.6000 SW","what":"90-degree elbow","x":500,"y":300}]}',
        "- ref: the number or tag as printed. " + WHAT[2:],
        "- x, y: where it sits on the drawing, in thousandths of the whole drawing width and height (Image 1) from its top-left corner. Point at the circle, diamond or box itself. Give x and y for every item.",
        "Never invent anything. If you are unsure of a value, use null.",
    ]
    return "\n".join(lines)


def norm_tables(a):
    out = []
    for t in a if isinstance(a, list) else []:
        try:
            c = lambda v: max(0.0, min(1000.0, float(v)))
            b = {"x0": c(t["x0"]), "y0": c(t["y0"]), "x1": c(t["x1"]), "y1": c(t["y1"]), "name": str(t.get("name", ""))}
        except (TypeError, KeyError, ValueError, AttributeError):
            continue
        if b["x1"] - b["x0"] < 30 or b["y1"] - b["y0"] < 30:
            continue
        b["area"] = (b["x1"] - b["x0"]) * (b["y1"] - b["y0"])
        out.append(b)
    out.sort(key=lambda b: (b["name"] == "title", -b["area"]))
    keep = []
    for b in out:
        dup = False
        for c in keep:
            w = min(b["x1"], c["x1"]) - max(b["x0"], c["x0"])
            h = min(b["y1"], c["y1"]) - max(b["y0"], c["y0"])
            if w > 0 and h > 0 and w * h > 0.7 * min(b["area"], c["area"]):
                dup = True
        if not dup:
            keep.append(b)
    return keep


def read_lists(im, ask, crop_blobs_ctx, extra=None):
    """Read the tables from the whole sheet, then the marks and tags on the drawing."""
    k = min(1.0, 4200 / max(im.size))
    base = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS) if k < 1 else im.copy()
    try:
        base = enhance(base)
    except Exception:
        pass
    W, H = base.size
    boxes = []
    try:
        boxes = norm_tables(_parse(ask(P6, [_jpeg(base, 1500, 92)], "quick")).get("tables"))
    except Exception:
        pass
    if boxes:
        imgs = []
        for b in boxes[:5]:
            mx, my = 0.02 * (b["x1"] - b["x0"]), 0.02 * (b["y1"] - b["y0"])
            x0, y0 = max(0, b["x0"] - mx) / 1000 * W, max(0, b["y0"] - my) / 1000 * H
            x1, y1 = min(1000, b["x1"] + mx) / 1000 * W, min(1000, b["y1"] + my) / 1000 * H
            t = base.crop((round(x0), round(y0), round(x1), round(y1)))
            sc = min(2.2, 2000 / max(t.size))
            t = t.resize((max(1, round(t.width * sc)), max(1, round(t.height * sc))), Image.LANCZOS)
            imgs.append(_jpeg(t, 2000, 92))
        focused = True
    else:
        imgs, c, r = make_set(im)
        focused = False
    d1 = _parse(ask(p5(len(imgs), focused), imgs, "deep"))
    items = list(d1.get("items") or [])
    line = d1.get("line")
    for eb in extra or []:  # extra photos of the material list / tables, read as close-ups
        try:
            ex = ImageOps.exif_transpose(Image.open(io.BytesIO(eb))).convert("RGB")
            try:
                ex = enhance(ex)
            except Exception:
                pass
            dx = _parse(ask(p5(1, True), [_jpeg(ex, 2000, 92)], "deep"))
            items += [x for x in (dx.get("items") or []) if isinstance(x, dict)]
            line = line or dx.get("line")
        except Exception:
            pass
    keep = ("pipe", "elbow", "tee", "reducer", "fitting", "valve", "flange", "gasket", "bolt", "instrument")
    ctx = "\n".join(
        "%s: %s" % (x.get("ref"), str(x.get("desc") or x.get("what") or "")[:60])
        for x in [x for x in items if isinstance(x, dict) and x.get("kind") in keep and x.get("ref")][:60]
    )
    blobs, c2, r2 = crop_blobs_ctx
    try:
        d2 = _parse(ask(p7(c2, r2, ctx), blobs, "deep"))
        items += list(d2.get("items") or [])
    except Exception:
        pass
    return {"line": line, "items": _dedupe(items)}


def _dedupe(items):
    """One circled mark per number: keep the one with a description, and a position if any."""
    out, seen = [], {}
    for x in items:
        if not isinstance(x, dict):
            continue
        if x.get("kind") == "mark" and x.get("ref") not in (None, ""):
            k = str(x["ref"]).strip()
            if k in seen:
                y = seen[k]
                for f in ("desc", "what", "size", "qty", "x", "y"):
                    if y.get(f) in (None, "") and x.get(f) not in (None, ""):
                        y[f] = x[f]
                continue
            seen[k] = x
        out.append(x)
    return out


def enhance(im):
    """Even out uneven light (divide by a blurred copy of the page) and deepen the ink a little."""
    import numpy as np

    w, h = im.size
    small = im.resize((max(4, round(w / 24)), max(4, round(h / 24))), Image.LANCZOS)
    bg = np.asarray(small.resize((w, h), Image.BILINEAR), dtype=np.float32)
    lum = 0.3 * bg[..., 0] + 0.59 * bg[..., 1] + 0.11 * bg[..., 2]
    gain = (248.0 / np.maximum(lum, 90.0))[..., None]
    a = np.minimum(np.asarray(im, dtype=np.float32) * gain, 255.0)
    out = 255.0 * (a / 255.0) ** 1.25
    return Image.fromarray(out.astype("uint8"), "RGB")


def make_set(crop, max_images=7):
    """-> (jpeg list, cols, rows): the whole drawing first, then zoomed tiles when the photo has the pixels."""
    w0, h0 = crop.size
    k = min(1.0, 3200 / max(w0, h0))
    base = crop.resize((max(1, round(w0 * k)), max(1, round(h0 * k))), Image.LANCZOS) if k < 1 else crop.copy()
    try:
        base = enhance(base)
    except Exception:
        pass
    W, H = base.size
    blobs = [_jpeg(base, 1500, 92)]
    cap, OV, best = min(6, max_images - 1), 0.12, None
    for c in (1, 2, 3):
        for r in (1, 2, 3):
            n = c * r
            if n < 2 or n > cap:
                continue
            tw, th = W / (c - (c - 1) * OV), H / (r - (r - 1) * OV)
            side = max(tw, th)
            if side < 380:
                continue
            if best is None or n > best[2] or (n == best[2] and side < best[5]):
                best = (c, r, n, tw, th, side)
    if not best:
        return blobs, 0, 0
    c, r, _, tw, th, side = best
    s = min(2.5, 1100 / side)
    for i in range(r):
        for j in range(c):
            tx = max(0.0, min(W - tw, j * tw * (1 - OV)))
            ty = max(0.0, min(H - th, i * th * (1 - OV)))
            t = base.crop((round(tx), round(ty), round(tx + tw), round(ty + th)))
            t = t.resize((max(1, round(tw * s)), max(1, round(th * s))), Image.LANCZOS)
            blobs.append(_jpeg(t, 2000, 92))
    return blobs, c, r


def _jpeg(im, max_side, quality=88):
    im = im.copy()
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _parse(text):
    """Read one JSON value out of a model reply, ignoring a sentence before or after it."""
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    if "```" in text:
        for part in text.split("```")[1::2]:
            part = part.split("\n", 1)[-1] if part[:4].lower().startswith("json") else part
            try:
                return json.loads(part)
            except ValueError:
                continue
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    ends = [i for i in (text.rfind("}"), text.rfind("]")) if i >= 0]
    if starts and ends and max(ends) > min(starts):
        return json.loads(text[min(starts) : max(ends) + 1])
    raise ValueError("no JSON in reply")


def _norm_box(b):
    try:
        x0, y0, x1, y1 = (max(0.0, min(1000.0, float(b[k]))) for k in ("x0", "y0", "x1", "y1"))
    except (TypeError, KeyError, ValueError):
        return None
    return (x0, y0, x1, y1) if x1 - x0 >= 100 and y1 - y0 >= 100 else None


def _rot_box(box, turns):
    """Move a box (thousandths of width/height) through `turns` clockwise quarter turns."""
    x0, y0, x1, y1 = box
    for _ in range(turns):
        x0, x1, y0, y1 = 1000 - y1, 1000 - y0, x0, x1
    return x0, y0, x1, y1


def read_iso(image_bytes, ask, extra=None):
    """ask(prompt, [jpeg_bytes, ...], tier) -> reply text. tier is "quick" or "deep".

    Returns what the page needs: the upright photo, where the drawing is on it (fractions of the
    photo), and Claude's trace of the pipe.
    """
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")

    turns, box = 0, None
    try:  # a bad reply here is not fatal: fall back to the whole sheet, as the page does
        r1 = _parse(ask(P1, [_jpeg(im, 1600)], "quick"))
        turns = int(round(float(r1.get("turns", 0)))) % 4
        box = _norm_box(r1.get("box"))
    except (ValueError, TypeError, AttributeError):
        pass

    if turns:
        im = im.rotate(-90 * turns, expand=True)  # Pillow turns counter-clockwise for positive angles
        if box:
            box = _rot_box(box, turns)

    fx0, fy0, fx1, fy1 = 0.0, 0.0, 1.0, 1.0
    if box:
        mx, my = 0.03 * (box[2] - box[0]), 0.03 * (box[3] - box[1])
        fx0, fx1 = max(0.0, box[0] - mx) / 1000, min(1000.0, box[2] + mx) / 1000
        fy0, fy1 = max(0.0, box[1] - my) / 1000, min(1000.0, box[3] + my) / 1000

    w, h = im.size
    crop = im.crop((round(fx0 * w), round(fy0 * h), round(fx1 * w), round(fy1 * h)))
    blobs, cols, rows = make_set(crop)
    notes = None
    try:  # the printed coordinates alone answer the question, so read them first and on their own
        notes = _parse(ask(p4(cols, rows), blobs, "deep"))
    except (ValueError, TypeError, AttributeError):
        pass

    items = None
    try:  # tables and drawing marks, for the checklist
        items = read_lists(im, ask, (blobs, cols, rows), extra)
    except Exception:
        pass

    read = None
    if not (notes and notes.get("notes")):  # no coordinates found: fall back to tracing the route
        try:
            read = _parse(ask(p2(cols, rows), blobs, "deep"))
        except Exception:
            if not items:
                raise

    page_img = base64.b64encode(_jpeg(im, 2000, 85)).decode()
    return {
        "img": "data:image/jpeg;base64," + page_img,
        "crop": {"x": fx0, "y": fy0, "w": fx1 - fx0, "h": fy1 - fy0},
        "read": read,
        "notes": notes,
        "list": items,
    }


def build_doc(page_html, preload=None):
    """The full HTML document for the iframe, with the optional reading baked in."""
    boot = "<script>window.__STREAMLIT=true;"
    if preload:
        boot += "window.__PRELOAD=" + json.dumps(preload).replace("</", "<\\/") + ";"
    boot += "</script>"
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        "<style>html,body{margin:0}</style></head><body>" + boot + page_html + "</body></html>"
    )
