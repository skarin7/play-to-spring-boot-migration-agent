"""dagre layout JSON -> PNG (visual verification) + .excalidraw (editable deliverable)."""
import json
import math
import random
import sys
from PIL import Image, ImageDraw, ImageFont

FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_R = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

COLORS = {"abort": "#c92a2a", "enter": "#9c36b5", "exit": "#6741d9"}
DOCS = "/home/shankar/Work/AI_Projects/play-to-spring-boot-migration-agent/docs"


def ecol(kind):
    return COLORS.get(kind, "#343a40")


def badge_geom(n, r=13):
    """Warning badge centred on the node's top-right corner."""
    return (n["x"] + n["width"] / 2 - r - 2, n["y"] - n["height"] / 2 + 2, r)


def self_loop_pts(n):
    """Small clean arc over the top of the node (dagre routes self-edges badly)."""
    x, y, w, h = n["x"], n["y"], n["width"], n["height"]
    top = y - h / 2
    return [(x - w * 0.22, top), (x - w * 0.22, top - 30), (x + w * 0.22, top - 30), (x + w * 0.22, top)]


# --------------------------------------------------------------------- PNG
def draw_layout(d, lay, ox, oy, fonts, node_index):
    f_node, f_nsub, f_edge, f_clu = fonts
    def T(x, y):
        return (x + ox, y + oy)

    for c in lay["clusters"]:
        x0, y0 = T(c["x"] - c["width"] / 2, c["y"] - c["height"] / 2)
        x1, y1 = T(c["x"] + c["width"] / 2, c["y"] + c["height"] / 2)
        d.rounded_rectangle([x0, y0, x1, y1], radius=14, outline=c["pal"]["stroke"], width=2)
        d.text((x0 + 12, y0 - 25), c["label"], font=f_clu, fill=c["pal"]["stroke"])

    def dash_seg(p1, p2, col, dash=9, gap=6):
        (ax, ay), (bx, by) = p1, p2
        dist = math.hypot(bx - ax, by - ay)
        if dist == 0:
            return
        ux, uy = (bx - ax) / dist, (by - ay) / dist
        t = 0.0
        while t < dist:
            e2 = min(t + dash, dist)
            d.line([(ax + ux * t, ay + uy * t), (ax + ux * e2, ay + uy * e2)], fill=col, width=2)
            t = e2 + gap

    def arrow_line(pts, col, dashed=False):
        if dashed:
            for a, b in zip(pts, pts[1:]):
                dash_seg(a, b, col)
        else:
            d.line(pts, fill=col, width=2, joint="curve")
        (x1, y1), (x2, y2) = pts[-2], pts[-1]
        ang = math.atan2(y2 - y1, x2 - x1)
        for s in (-0.42, 0.42):
            d.line([(x2, y2), (x2 - 12 * math.cos(ang + s), y2 - 12 * math.sin(ang + s))], fill=col, width=2)

    def label_at(lx, ly, txt, col):
        tw = d.textlength(txt, font=f_edge)
        d.rectangle([lx - tw / 2 - 4, ly - 10, lx + tw / 2 + 4, ly + 10], fill="white")
        d.text((lx - tw / 2, ly - 8), txt, font=f_edge, fill=col)

    for e in lay["edges"]:
        pts = [T(p["x"], p["y"]) for p in e["points"]]
        col = ecol(e.get("kind"))
        arrow_line(pts, col, dashed=(e.get("kind") == "abort"))
        if e.get("label"):
            label_at(*T(e["x"], e["y"]), e["label"], col)

    for sl in lay.get("selfLoops", []):
        n = node_index[sl["id"]]
        pts = [T(*p) for p in self_loop_pts(n)]
        col = n["pal"]["stroke"]
        arrow_line(pts, col)
        if sl.get("label"):
            label_at(pts[1][0] + (pts[2][0] - pts[1][0]) / 2, pts[1][1] - 12, sl["label"], col)

    rail = lay.get("abortRail")
    if rail:
        col = COLORS["abort"]
        ry = rail["y"] + oy

        def dashed(p1, p2, dash=9, gap=6):
            (ax, ay), (bx, by) = p1, p2
            dist = math.hypot(bx - ax, by - ay)
            if dist == 0:
                return
            ux, uy = (bx - ax) / dist, (by - ay) / dist
            t = 0.0
            while t < dist:
                e = min(t + dash, dist)
                d.line([(ax + ux * t, ay + uy * t), (ax + ux * e, ay + uy * e)], fill=col, width=2)
                t = e + gap

        for s in rail["stubs"]:
            dashed(T(s["x"], s["from"]), T(s["x"], rail["y"]))
            d.ellipse([T(s["x"], rail["y"])[0] - 4, ry - 4, T(s["x"], rail["y"])[0] + 4, ry + 4], fill=col)
        dashed(T(rail["x0"], rail["y"]), T(rail["x1"], rail["y"]))
        ax, ay = T(rail["x1"], rail["y"])
        for s in (-0.42, 0.42):
            d.line([(ax, ay), (ax - 12 * math.cos(s), ay - 12 * math.sin(s))], fill=col, width=2)
        label_at(*T((rail["x0"] + rail["x1"]) / 2, rail["y"] - 16), rail["label"], col)

    for n in lay["nodes"]:
        x0, y0 = T(n["x"] - n["width"] / 2, n["y"] - n["height"] / 2)
        x1, y1 = T(n["x"] + n["width"] / 2, n["y"] + n["height"] / 2)
        d.rounded_rectangle([x0, y0, x1, y1], radius=10, fill=n["pal"]["bg"], outline=n["pal"]["stroke"], width=2)
        cx = (x0 + x1) / 2
        lines = n["label"].split("\n")
        if n["sub"]:
            sub_lines = n["sub"].split("\n")
            block_h = 18 * len(lines) + 3 + 15 * len(sub_lines)
            ty = (y0 + y1) / 2 - block_h / 2
            for ln in lines:
                tw = d.textlength(ln, font=f_node)
                d.text((cx - tw / 2, ty), ln, font=f_node, fill=n["pal"]["stroke"])
                ty += 18
            ty += 3
            for ln in sub_lines:
                tw2 = d.textlength(ln, font=f_nsub)
                d.text((cx - tw2 / 2, ty), ln, font=f_nsub, fill="#495057")
                ty += 15
        else:
            ty = (y0 + y1) / 2 - 9 * len(lines)
            for ln in lines:
                tw = d.textlength(ln, font=f_node)
                d.text((cx - tw / 2, ty), ln, font=f_node, fill=n["pal"]["stroke"])
                ty += 18
        if n.get("warn"):
            bx, by, r = badge_geom(n)
            bcx, bcy = T(bx, by)
            col = COLORS["abort"]
            d.ellipse([bcx - r, bcy - r, bcx + r, bcy + r], fill=col, outline="white", width=2)
            tw = d.textlength("!", font=f_node)
            d.text((bcx - tw / 2, bcy - 10), "!", font=f_node, fill="white")


def render_png(panels, out_png, title, subtitle):
    fonts_probe = ImageFont.truetype(FONT_R, 12)
    pad, gap, head = 40, 90, 130
    W = max(p["layout"]["graph"]["width"] for p in panels) + pad * 2
    H = head + sum(p["layout"]["graph"]["height"] + gap for p in panels) + pad
    img = Image.new("RGB", (int(W), int(H)), "white")
    d = ImageDraw.Draw(img)
    fonts = (ImageFont.truetype(FONT_B, 15), ImageFont.truetype(FONT_R, 12),
             ImageFont.truetype(FONT_R, 12), ImageFont.truetype(FONT_B, 15))
    d.text((pad, 28), title, font=ImageFont.truetype(FONT_B, 30), fill="#1e1e1e")
    d.text((pad, 70), subtitle, font=ImageFont.truetype(FONT_R, 15), fill="#868e96")

    y = head
    for p in panels:
        lay = p["layout"]
        if p.get("title"):
            d.text((pad, y - 30), p["title"], font=ImageFont.truetype(FONT_B, 17), fill="#1e1e1e")
        idx = {n["id"]: n for n in lay["nodes"]}
        draw_layout(d, lay, pad, y, fonts, idx)
        if lay.get("legend"):
            ly = y + lay["graph"]["height"] + 6
            r = 11
            d.ellipse([pad + r - 11, ly - 1, pad + r + 11, ly + 21], fill=COLORS["abort"], outline="white", width=2)
            d.text((pad + r - 3, ly + 2), "!", font=fonts[0], fill="white")
            d.text((pad + 34, ly + 3), lay["legend"], font=ImageFont.truetype(FONT_R, 14), fill="#495057")
        y += lay["graph"]["height"] + gap
    img.save(out_png)
    return int(W), int(H)


# -------------------------------------------------------------- excalidraw
def rid():
    return "".join(random.choice("0123456789abcdef") for _ in range(16))


def ex_rect(els, x, y, w, h, bg, stroke, dashed=False):
    els.append({"id": rid(), "type": "rectangle", "x": x, "y": y, "width": w, "height": h, "angle": 0,
                "strokeColor": stroke, "backgroundColor": bg, "fillStyle": "solid", "strokeWidth": 2,
                "strokeStyle": "dashed" if dashed else "solid", "roughness": 0 if dashed else 1,
                "opacity": 100, "groupIds": [], "frameId": None, "roundness": {"type": 3},
                "seed": random.randint(1, 2**31), "version": 1, "versionNonce": random.randint(1, 2**31),
                "isDeleted": False, "boundElements": [], "updated": 1, "link": None, "locked": False})


def ex_ellipse(els, x, y, w, h, bg, stroke):
    els.append({"id": rid(), "type": "ellipse", "x": x, "y": y, "width": w, "height": h, "angle": 0,
                "strokeColor": stroke, "backgroundColor": bg, "fillStyle": "solid", "strokeWidth": 2,
                "strokeStyle": "solid", "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None,
                "roundness": None, "seed": random.randint(1, 2**31), "version": 1,
                "versionNonce": random.randint(1, 2**31), "isDeleted": False, "boundElements": [],
                "updated": 1, "link": None, "locked": False})


def ex_text(els, x, y, w, label, size, color, align="center", bg="transparent"):
    els.append({"id": rid(), "type": "text", "x": x, "y": y, "width": w,
                "height": size * 1.3 * (label.count("\n") + 1), "angle": 0, "strokeColor": color,
                "backgroundColor": bg, "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid",
                "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None, "roundness": None,
                "seed": random.randint(1, 2**31), "version": 1, "versionNonce": random.randint(1, 2**31),
                "isDeleted": False, "boundElements": [], "updated": 1, "link": None, "locked": False,
                "text": label, "fontSize": size, "fontFamily": 1, "textAlign": align,
                "verticalAlign": "top", "baseline": size, "containerId": None,
                "originalText": label, "lineHeight": 1.3})


def ex_arrow(els, points, stroke, dashed=False):
    x0, y0 = points[0]
    rel = [[px - x0, py - y0] for px, py in points]
    els.append({"id": rid(), "type": "arrow", "x": x0, "y": y0,
                "width": max(abs(p[0]) for p in rel) or 1, "height": max(abs(p[1]) for p in rel) or 1,
                "angle": 0, "strokeColor": stroke, "backgroundColor": "transparent", "fillStyle": "solid",
                "strokeWidth": 2, "strokeStyle": "dashed" if dashed else "solid", "roughness": 1,
                "opacity": 100, "groupIds": [], "frameId": None, "roundness": {"type": 2},
                "seed": random.randint(1, 2**31), "version": 1, "versionNonce": random.randint(1, 2**31),
                "isDeleted": False, "boundElements": [], "updated": 1, "link": None, "locked": False,
                "points": rel, "lastCommittedPoint": None, "startBinding": None, "endBinding": None,
                "startArrowhead": None, "endArrowhead": "arrow"})


def emit_excalidraw(panels, out_path, title, subtitle):
    els = []
    pad, gap, head = 40, 90, 130
    ex_text(els, pad, 28, 1600, title, 28, "#1e1e1e", align="left")
    ex_text(els, pad, 70, 1600, subtitle, 15, "#868e96", align="left")
    y = head
    for p in panels:
        lay = p["layout"]
        if p.get("title"):
            ex_text(els, pad, y - 32, 1600, p["title"], 17, "#1e1e1e", align="left")
        idx = {n["id"]: n for n in lay["nodes"]}
        oy = y
        for c in lay["clusters"]:
            ex_rect(els, c["x"] - c["width"] / 2 + pad, c["y"] - c["height"] / 2 + oy,
                    c["width"], c["height"], "transparent", c["pal"]["stroke"], dashed=True)
            ex_text(els, c["x"] - c["width"] / 2 + pad + 12, c["y"] - c["height"] / 2 + oy - 26,
                    c["width"] * 1.5, c["label"], 15, c["pal"]["stroke"], align="left")
        for e in lay["edges"]:
            pts = [(pp["x"] + pad, pp["y"] + oy) for pp in e["points"]]
            col = ecol(e.get("kind"))
            ex_arrow(els, pts, col, dashed=(e.get("kind") == "abort"))
            if e.get("label"):
                ex_text(els, e["x"] + pad - 75, e["y"] + oy - 8, 150, e["label"], 12, col, bg="#ffffff")
        for sl in lay.get("selfLoops", []):
            n = idx[sl["id"]]
            pts = [(px + pad, py + oy) for px, py in self_loop_pts(n)]
            col = n["pal"]["stroke"]
            ex_arrow(els, pts, col)
            if sl.get("label"):
                ex_text(els, (pts[1][0] + pts[2][0]) / 2 - 75, pts[1][1] - 20, 150, sl["label"], 12, col, bg="#ffffff")
        rail = lay.get("abortRail")
        if rail:
            col = COLORS["abort"]
            for s in rail["stubs"]:
                ex_arrow(els, [(s["x"] + pad, s["from"] + oy), (s["x"] + pad, rail["y"] + oy)], col, dashed=True)
            ex_arrow(els, [(rail["x0"] + pad, rail["y"] + oy), (rail["x1"] + pad, rail["y"] + oy)], col, dashed=True)
            ex_text(els, (rail["x0"] + rail["x1"]) / 2 + pad - 350, rail["y"] + oy - 26, 700,
                    rail["label"], 12, col, bg="#ffffff")

        for n in lay["nodes"]:
            nx = n["x"] - n["width"] / 2 + pad
            ny = n["y"] - n["height"] / 2 + oy
            ex_rect(els, nx, ny, n["width"], n["height"], n["pal"]["bg"], n["pal"]["stroke"])
            if n["sub"]:
                ex_text(els, nx + 6, ny + 10, n["width"] - 12, n["label"], 15, n["pal"]["stroke"])
                ex_text(els, nx + 6, ny + 10 + 20 * (n["label"].count("\n") + 1), n["width"] - 12, n["sub"], 11, "#495057")
            else:
                ex_text(els, nx + 6, ny + n["height"] / 2 - 9, n["width"] - 12, n["label"], 15, n["pal"]["stroke"])
            if n.get("warn"):
                bx, by, r = badge_geom(n)
                ex_ellipse(els, bx + pad - r, by + oy - r, r * 2, r * 2, COLORS["abort"], "#ffffff")
                ex_text(els, bx + pad - r, by + oy - 9, r * 2, "!", 15, "#ffffff")
        if lay.get("legend"):
            ly = y + lay["graph"]["height"] + 6
            ex_ellipse(els, pad, ly, 22, 22, COLORS["abort"], "#ffffff")
            ex_text(els, pad, ly + 2, 22, "!", 15, "#ffffff")
            ex_text(els, pad + 34, ly + 4, 1400, lay["legend"], 14, "#495057", align="left")
        y += lay["graph"]["height"] + gap
    scene = {"type": "excalidraw", "version": 2, "source": "https://excalidraw.com", "elements": els,
             "appState": {"gridSize": 20, "viewBackgroundColor": "#ffffff"}, "files": {}}
    with open(out_path, "w") as f:
        json.dump(scene, f, indent=2)
    return len(els)


if __name__ == "__main__":
    which = sys.argv[1]
    raw = json.load(open(f"layout-{which}.json"))
    if which == "detailed":
        panels = raw["panels"]
        title = "LangGraph migration engine — detailed node/edge map"
        sub = ("All 26 nodes and every edge from graph.py build_graph(). Dashed red = abort into run_halt; "
               "purple = enter/exit the shared compile-fix subgraph.")
    else:
        panels = [{"title": None, "layout": raw}]
        title = "LangGraph migration engine — high-level loop"
        sub = ("Five stages; each merges several graph nodes. ↺ = the stage really loops. "
               "The run ends in exactly one of two outcomes. All 26 nodes: see the detailed map.")
    w, h = render_png(panels, f"{DOCS}/langgraph-engine-flow-{which}.png", title, sub)
    n = emit_excalidraw(panels, f"{DOCS}/langgraph-engine-flow-{which}.excalidraw", title, sub)
    print(f"{which}: png {w}x{h}, excalidraw elements {n}")
