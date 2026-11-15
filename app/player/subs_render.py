"""Σχεδίαση υποτίτλων (κειμένου και εικόνας) πάνω στο καρέ, με cache."""
import io


class SubtitleRenderer:
    def __init__(self, pygame):
        self.pg = pygame
        self.cache = {}
        self.fonts = {}

    def _font(self, size, italic):
        f = self.fonts.get((size, italic))
        if f is None:
            f = self.pg.font.SysFont("arial,segoeui", size, bold=True, italic=italic)
            self.fonts[(size, italic)] = f
        return f

    def text_surface(self, line, italic, size):
        """Λευκό κείμενο με μαύρο περίγραμμα."""
        key = (line, italic, size)
        s = self.cache.get(key)
        if s is not None:
            return s
        f = self._font(size, italic)
        fg = f.render(line, True, (255, 255, 255))
        bg = f.render(line, True, (0, 0, 0))
        o = max(1, size // 14)
        s = self.pg.Surface((fg.get_width() + 2 * o, fg.get_height() + 2 * o), self.pg.SRCALPHA)
        for dx in (-o, 0, o):
            for dy in (-o, 0, o):
                if dx or dy:
                    s.blit(bg, (o + dx, o + dy))
        s.blit(fg, (o, o))
        if len(self.cache) > 300:
            self.cache.clear()
        self.cache[key] = s
        return s

    def image_surface(self, png, scale):
        key = (id(png), round(scale, 3))
        s = self.cache.get(key)
        if s is None:
            s = self.pg.image.load(io.BytesIO(png), "sub.png").convert_alpha()
            if abs(scale - 1) > 0.01:
                s = self.pg.transform.smoothscale(s, (max(1, int(s.get_width() * scale)),
                                                      max(1, int(s.get_height() * scale))))
            self.cache[key] = s
        return s

    @staticmethod
    def _image_pos(it, s, pic):
        if it["halign"] == "left":
            x = pic.x + int(pic.w * it["hpos"] / 100)
        elif it["halign"] == "right":
            x = pic.right - int(pic.w * it["hpos"] / 100) - s.get_width()
        else:
            x = pic.centerx - s.get_width() // 2 + int(pic.w * it["hpos"] / 100)
        if it["valign"] == "top":
            y = pic.y + int(pic.h * it["vpos"] / 100)
        elif it["valign"] == "center":
            y = pic.centery - s.get_height() // 2 + int(pic.h * it["vpos"] / 100)
        else:
            y = pic.bottom - int(pic.h * it["vpos"] / 100) - s.get_height()
        return x, y

    def draw(self, screen, track, t, pic, src_w, bottom):
        """track: {"entries": [(αρχή, τέλος, items)]}. bottom: πού τελειώνει ο ελεύθερος χώρος (μπάρα)."""
        if bottom < pic.bottom:  # η μπάρα καλύπτει το κάτω μέρος: ανεβάζουμε τους υπότιτλους
            pic = self.pg.Rect(pic.x, pic.y, pic.w, max(10, bottom - pic.y))
        active = [e for e in track["entries"] if e[0] <= t < e[1]]
        if not active:
            return
        size = max(14, int(pic.h * 0.055))
        for _a, _b, items in active:
            for it in items:
                if it["type"] == "image":
                    s = self.image_surface(it["png"], pic.w / max(1, src_w))
                    screen.blit(s, self._image_pos(it, s, pic))
            texts = [it for it in items if it["type"] == "text"]
            if texts:
                self._draw_text_block(screen, texts, pic, size)

    def _draw_text_block(self, screen, texts, pic, size):
        surfs = [self.text_surface(it["text"], it["italic"], size) for it in texts]
        if all(it["y"] < 40 for it in texts):  # στοίχιση πάνω
            y = pic.y + int(pic.h * 0.06)
        else:
            y = pic.bottom - int(pic.h * 0.06) - sum(s.get_height() for s in surfs)
        for s in surfs:
            screen.blit(s, (pic.centerx - s.get_width() // 2, y))
            y += s.get_height()
