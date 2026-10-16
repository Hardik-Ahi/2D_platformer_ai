import json
import math
import os
import random
import sys

import pygame

W, H = 960, 540
FPS = 60
TILE = 40
GRAVITY = 0.6
JUMP_V = -13.5
MAX_JUMPS = 2
SLASH_R = 64
SLASH_FRAMES = 10
SPEED = 5
CHUNK = 20 * TILE  # world chunk width in pixels
GROUND_Y = H - 2 * TILE


def chunk_content(idx):
    """Deterministic platforms for a chunk so backtracking shows the same world."""
    rng = random.Random(idx * 7919 + 13)
    platforms = []
    x0 = idx * CHUNK
    if idx <= 0:
        platforms.append(pygame.Rect(x0, GROUND_Y, CHUNK, 2 * TILE))
        return platforms
    x = x0
    end = x0 + CHUNK
    while x < end:
        seg = rng.randint(4, 9) * TILE
        seg = min(seg, end - x)
        platforms.append(pygame.Rect(x, GROUND_Y, seg, 2 * TILE))
        x += seg
        if x < end:
            x += rng.randint(2, 3) * TILE  # jumpable pit
    for _ in range(rng.randint(1, 3)):
        w = rng.randint(3, 5) * TILE
        px = x0 + rng.randint(0, CHUNK - w)
        # bottom edge sits ~220px above ground: a full single jump (~150px) plus the 44px body fits underneath
        py = GROUND_Y - rng.choice([240, 250, 260])
        platforms.append(pygame.Rect(px, py, w, TILE // 2))
    return platforms


def save_path():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "CoinSlasher", "highscore.json")


def load_high_score():
    try:
        with open(save_path(), "r", encoding="utf-8") as f:
            v = json.load(f)["high_score"]
        return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def save_high_score(value):
    path = save_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"high_score": int(value)}, f)
        os.replace(tmp, path)
    except OSError:
        pass  # never crash the game over a failed save


def draw_sword(surface, hand, tip):
    """Draw a tapered steel sword from the hilt position to its tip."""
    dx, dy = tip[0] - hand[0], tip[1] - hand[1]
    length = math.hypot(dx, dy)
    if length < 1:
        return
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    guard_x, guard_y = hand[0] + ux * 8, hand[1] + uy * 8
    blade_base_x, blade_base_y = guard_x + ux * 2, guard_y + uy * 2

    def offset(x, y, along, across):
        return (int(round(x + ux * along + px * across)),
                int(round(y + uy * along + py * across)))

    # Grip and pommel. ``hand`` is the character's grip point at the guard.
    pommel = (hand[0] - ux * 8, hand[1] - uy * 8)
    pygame.draw.line(surface, (72, 43, 31), pommel, (int(guard_x), int(guard_y)), 5)
    for d in (3, 6):
        pygame.draw.line(surface, (177, 117, 62), offset(pommel[0], pommel[1], d, -2),
                         offset(pommel[0], pommel[1], d + 2, 2), 1)
    pygame.draw.circle(surface, (236, 187, 81), (int(pommel[0]), int(pommel[1])), 3)

    # Crossguard.
    pygame.draw.line(surface, (130, 77, 39), offset(guard_x, guard_y, 0, -8),
                     offset(guard_x, guard_y, 0, 8), 4)
    pygame.draw.line(surface, (255, 218, 117), offset(guard_x, guard_y, 0, -7),
                     offset(guard_x, guard_y, 0, 7), 2)

    # Tapered blade with a bright edge and fuller.
    blade_len = max(1, length - 10)
    base = (blade_base_x, blade_base_y)
    blade_tip = (hand[0] + ux * (length), hand[1] + uy * (length))
    blade_poly = [offset(base[0], base[1], 0, -3), offset(base[0], base[1], 0, 3),
                  offset(hand[0], hand[1], blade_len - 3, 2),
                  offset(blade_tip[0], blade_tip[1], 0, 0),
                  offset(hand[0], hand[1], blade_len - 3, -2)]
    pygame.draw.polygon(surface, (181, 199, 211), blade_poly)
    pygame.draw.line(surface, (249, 253, 255), offset(*base, 0, -2),
                     offset(*blade_tip, 0, 0), 1)
    pygame.draw.line(surface, (117, 142, 160), offset(*base, 0, 1),
                     offset(hand[0], hand[1], blade_len - 5, 0), 1)


class Game:
    def __init__(self):
        self.high_score = load_high_score()
        self.reset()

    def reset(self):
        self.px, self.py = 100.0, GROUND_Y - 60.0
        self.vx = self.vy = 0.0
        self.pw, self.ph = 26, 44
        self.facing = 1
        self.on_ground = False
        self.jumps_left = MAX_JUMPS
        self.chunks = {}
        self.score = 0
        self.coins = 0
        self.new_record = False
        self.bats = []
        self.bat_timer = 120
        self.slash_t = 0
        self.slash_cd = 0
        self.dead = False
        self.death_reason = ""
        self.particles = []
        self.best_x = 0
        self.tick = 0

    def chunk(self, i):
        if i not in self.chunks:
            self.chunks[i] = chunk_content(i)
        return self.chunks[i]

    def nearby_platforms(self):
        c = int(self.px // CHUNK)
        out = []
        for i in (c - 1, c, c + 1):
            out += self.chunk(i)
        return out

    def rect(self):
        return pygame.Rect(int(self.px), int(self.py), self.pw, self.ph)

    def slash_center(self):
        return self.px + self.pw / 2, self.py + self.ph / 2

    def hits_slash(self, rect):
        # half-disc in front of the player, centred on the body; covers above the head and below the feet
        cx, cy = self.slash_center()
        if self.facing > 0 and rect.right <= cx:
            return False
        if self.facing < 0 and rect.left >= cx:
            return False
        nx = min(max(cx, rect.left), rect.right)
        ny = min(max(cy, rect.top), rect.bottom)
        return math.hypot(nx - cx, ny - cy) <= SLASH_R

    def slash_point(self, prog):
        """Blade tip: sweeps from straight up, round the front, to straight down, curling in at the end."""
        cx, cy = self.slash_center()
        ang = -math.pi / 2 + math.pi * prog
        curl = max(0.0, (prog - 0.75) / 0.25)
        rad = SLASH_R * (1 - 0.55 * curl * curl)
        return cx + self.facing * math.cos(ang) * rad, cy + math.sin(ang) * rad

    def die(self, reason):
        self.dead = True
        self.death_reason = reason
        self.new_record = self.coins > self.high_score
        if self.new_record:
            self.high_score = self.coins
            save_high_score(self.high_score)

    def burst(self, x, y, color):
        for _ in range(14):
            a = random.uniform(0, math.tau)
            s = random.uniform(1, 5)
            self.particles.append([x, y, math.cos(a) * s, math.sin(a) * s, 25, color])

    def update(self, keys, jump_pressed=False):
        self.tick += 1
        if self.dead:
            return
        left = keys[pygame.K_a]
        right = keys[pygame.K_d]
        self.vx = (right - left) * SPEED
        if self.vx:
            self.facing = 1 if self.vx > 0 else -1
        if self.on_ground:
            self.jumps_left = MAX_JUMPS
        elif self.jumps_left == MAX_JUMPS:
            self.jumps_left = MAX_JUMPS - 1  # walked off a ledge: only the air jump remains
        if jump_pressed and self.jumps_left > 0:
            self.vy = JUMP_V
            self.jumps_left -= 1
            self.on_ground = False
            self.burst(self.px + self.pw / 2, self.py + self.ph, (255, 255, 255))
        self.vy = min(self.vy + GRAVITY, 18)

        plats = self.nearby_platforms()
        # horizontal move; world starts at x=0 wall
        self.px = max(0, self.px + self.vx)
        r = self.rect()
        for p in plats:
            if r.colliderect(p):
                if self.vx > 0:
                    self.px = p.left - self.pw
                elif self.vx < 0:
                    self.px = p.right
                r = self.rect()
        # vertical move
        self.py += self.vy
        r = self.rect()
        self.on_ground = False
        for p in plats:
            if r.colliderect(p):
                if self.vy > 0:
                    self.py = p.top - self.ph
                    self.on_ground = True
                elif self.vy < 0:
                    self.py = p.bottom
                self.vy = 0
                r = self.rect()

        # stable ground probe: standing exactly on a surface still counts as grounded
        if not self.on_ground and self.vy >= 0:
            probe = self.rect().move(0, 1)
            self.on_ground = any(probe.colliderect(p) for p in plats)

        if self.py > H + 50:
            self.die("You fell!")
            return

        # camera: driven only by player position
        target = self.px - W * 0.4
        self.cam = max(0, target)

        # sword
        if self.slash_cd > 0:
            self.slash_cd -= 1
        if self.slash_t > 0:
            self.slash_t -= 1
        if (keys[pygame.K_RETURN] or keys[pygame.K_KP_ENTER]) and self.slash_cd == 0:
            self.slash_t = SLASH_FRAMES
            self.slash_cd = 22
        if self.slash_t > 0:
            for b in self.bats[:]:
                if self.hits_slash(b["rect"]()):
                    self.bats.remove(b)
                    self.score += 5
                    self.coins += 1
                    self.burst(b["x"], b["y"] + 12, (255, 215, 0))
                    self.burst(b["x"], b["y"], (180, 80, 220))

        # bats spawn ahead of the player (in the direction of travel view: right edge)
        self.bat_timer -= 1
        if self.bat_timer <= 0:
            self.bat_timer = random.choice([random.randint(30, 90), random.randint(90, 260)])
            by = random.randint(60, GROUND_Y - 30)
            self.bats.append({"x": self.cam + W + 40, "y": by, "base": by, "target": by,
                              "ph": random.uniform(0, 6), "spd": random.uniform(2, 6),
                              "amp": random.uniform(10, 70), "freq": random.uniform(0.04, 0.14),
                              "burst": 0, "rect": None})
            b = self.bats[-1]
            b["rect"] = lambda b=b: pygame.Rect(int(b["x"]) - 16, int(b["y"]) - 10, 32, 32)
        pr = self.rect()
        for b in self.bats[:]:
            if random.random() < 0.015:
                b["target"] = random.randint(60, GROUND_Y - 30)
            if b["burst"] <= 0 and random.random() < 0.01:
                b["burst"] = random.randint(15, 40)
                b["mult"] = random.choice([0.2, 2.0])
            mult = 1.0
            if b["burst"] > 0:
                b["burst"] -= 1
                mult = b["mult"]
            b["base"] += (b["target"] - b["base"]) * 0.03
            b["x"] -= b["spd"] * mult
            b["y"] = b["base"] + math.sin(self.tick * b["freq"] + b["ph"]) * b["amp"]
            if b["x"] < self.cam - 200:
                self.bats.remove(b)
            elif pr.colliderect(b["rect"]()):
                self.die("A bat got you!")
                return

        for p in self.particles[:]:
            p[0] += p[2]; p[1] += p[3]; p[4] -= 1
            if p[4] <= 0:
                self.particles.remove(p)

    def draw_player(self, s, cam):
        """Draw a compact, direction-aware cartoon adventurer over the physics body."""
        r = self.rect().move(-int(cam), 0)
        cx = r.centerx
        top = r.top
        facing = self.facing

        def point(x, y):
            return cx + facing * x, top + y

        def box(x1, y1, x2, y2):
            a = point(x1, y1)
            b = point(x2, y2)
            return pygame.Rect(min(a[0], b[0]), min(a[1], b[1]),
                               abs(a[0] - b[0]), abs(a[1] - b[1]))

        running = self.on_ground and abs(self.vx) > 0
        phase = self.tick * 0.32 if running else 0
        leg_swing = math.sin(phase) * 3 if running else 0
        arm_swing = math.sin(phase + math.pi) * 2 if running else 0
        airborne = not self.on_ground

        # Legs, trousers and boots are drawn behind the tunic.
        pygame.draw.line(s, (48, 55, 78), point(-5, 32), point(-6 - leg_swing, 40), 5)
        pygame.draw.line(s, (48, 55, 78), point(5, 32), point(6 + leg_swing, 40), 5)
        pygame.draw.ellipse(s, (37, 39, 53), box(-12 - leg_swing, 37, -2 - leg_swing, 44))
        pygame.draw.ellipse(s, (37, 39, 53), box(2 + leg_swing, 37, 12 + leg_swing, 44))
        pygame.draw.line(s, (93, 57, 37), point(-6 - leg_swing, 42), point(-1 - leg_swing, 42), 2)
        pygame.draw.line(s, (93, 57, 37), point(6 + leg_swing, 42), point(11 + leg_swing, 42), 2)

        # Arms: the far arm sits behind the torso; the forward arm carries the sword.
        far_arm_y = 21 + (arm_swing if not airborne else -3)
        near_arm_y = 21 - (arm_swing if not airborne else 3)
        pygame.draw.line(s, (42, 91, 117), point(-8, 17), point(-13, far_arm_y + 7), 5)
        pygame.draw.circle(s, (238, 183, 133), tuple(map(int, point(-13, far_arm_y + 8))), 3)

        # Tunic, collar, belt and bright stitching.
        pygame.draw.rect(s, (25, 94, 119), box(-10, 15, 10, 35), border_radius=5)
        pygame.draw.rect(s, (36, 130, 148), box(-7, 17, 7, 32), border_radius=4)
        pygame.draw.line(s, (255, 215, 116), point(-8, 18), point(0, 23), 2)
        pygame.draw.line(s, (255, 215, 116), point(8, 18), point(0, 23), 2)
        pygame.draw.rect(s, (94, 55, 36), box(-10, 29, 10, 33), border_radius=2)
        pygame.draw.rect(s, (245, 194, 68), box(-2, 29, 3, 33), border_radius=1)
        pygame.draw.circle(s, (255, 231, 151), tuple(map(int, point(0, 30))), 1)

        # Face, hair and a simple leather headband.
        pygame.draw.ellipse(s, (238, 183, 133), box(-8, 2, 8, 18))
        pygame.draw.ellipse(s, (201, 130, 88), box(-8, 11, 8, 18))
        pygame.draw.polygon(s, (61, 39, 35), [point(-9, 8), point(-8, 2), point(-4, 0),
                                              point(3, 1), point(8, 5), point(8, 9),
                                              point(4, 6), point(0, 8), point(-4, 6)])
        pygame.draw.line(s, (195, 73, 53), point(-8, 6), point(8, 6), 2)
        pygame.draw.circle(s, (247, 218, 160), tuple(map(int, point(8, 6))), 2)
        # Eye and eyebrow face toward the current movement direction.
        pygame.draw.line(s, (61, 39, 35), point(2, 8), point(7, 8), 2)
        pygame.draw.circle(s, (34, 40, 48), tuple(map(int, point(5, 10))), 1)
        pygame.draw.line(s, (120, 67, 53), point(3, 14), point(6, 14), 1)

        shoulder = point(8, 17)
        if self.slash_t > 0:
            prog = 1 - (self.slash_t - 1) / SLASH_FRAMES
            angle = -math.pi / 2 + math.pi * prog
            arm_dx = facing * math.cos(angle)
            arm_dy = math.sin(angle)
            elbow = (shoulder[0] + arm_dx * 6, shoulder[1] + arm_dy * 6)
            hand = (shoulder[0] + arm_dx * 13, shoulder[1] + arm_dy * 13)

            pygame.draw.line(s, (25, 94, 119), shoulder, elbow, 7)
            pygame.draw.line(s, (238, 183, 133), elbow, hand, 5)
            pygame.draw.circle(s, (251, 210, 132), tuple(map(int, hand)), 4)
            tip = (hand[0] + arm_dx * 37, hand[1] + arm_dy * 37)
            draw_sword(s, hand, tip)
        else:
            hand = point(14, near_arm_y + 8)
            pygame.draw.line(s, (25, 94, 119), point(8, 17), point(12, near_arm_y + 6), 6)
            pygame.draw.line(s, (238, 183, 133), point(12, near_arm_y + 5), hand, 5)
            pygame.draw.circle(s, (251, 210, 132), tuple(map(int, hand)), 4)
            tip = point(14 + 48, near_arm_y - 9)
            draw_sword(s, hand, tip)

    def draw(self, s, font):
        s.fill((110, 190, 240))
        cam = int(self.cam)
        # parallax hills
        for layer, (f, col, h) in enumerate([(0.2, (140, 200, 160), 160), (0.5, (90, 160, 110), 100)]):
            off = int(cam * f)
            for k in range(-1, W // 200 + 3):
                x = k * 200 - off % 200
                pygame.draw.circle(s, col, (x + 100, H - 80 + h // 2), h)
        c = int(self.px // CHUNK)
        for i in (c - 1, c, c + 1, c + 2):
            for p in self.chunk(i):
                q = p.move(-cam, 0)
                pygame.draw.rect(s, (120, 80, 40), q)
                pygame.draw.rect(s, (60, 170, 70), (q.x, q.y, q.w, 8))
        for b in self.bats:
            x, y = int(b["x"] - cam), int(b["y"])
            flap = math.sin(self.tick * 0.4) * 10
            pygame.draw.polygon(s, (60, 30, 80), [(x, y), (x - 20, y - 10 + flap), (x - 8, y + 6)])
            pygame.draw.polygon(s, (60, 30, 80), [(x, y), (x + 20, y - 10 + flap), (x + 8, y + 6)])
            pygame.draw.circle(s, (90, 50, 110), (x, y), 8)
            pygame.draw.circle(s, (255, 60, 60), (x - 3, y - 2), 2)
            # gold sack gripped in the bat's feet
            pygame.draw.line(s, (60, 30, 80), (x - 3, y + 7), (x - 3, y + 11), 2)
            pygame.draw.line(s, (60, 30, 80), (x + 3, y + 7), (x + 3, y + 11), 2)
            pygame.draw.circle(s, (230, 175, 20), (x, y + 15), 7)
            pygame.draw.circle(s, (255, 225, 80), (x - 2, y + 13), 3)
            pygame.draw.rect(s, (150, 100, 20), (x - 3, y + 8, 6, 3))
        if not self.dead or self.death_reason != "You fell!":
            if self.slash_t > 0:
                prog = 1 - (self.slash_t - 1) / SLASH_FRAMES
                cx, cy = self.slash_center()
                pts = [self.slash_point(prog * k / 12) for k in range(13)]
                pts = [(x - cam, y) for x, y in pts]
                for k in range(1, len(pts)):
                    width = 2 + int(8 * k / len(pts))
                    pygame.draw.line(s, (255, 255, 255), pts[k - 1], pts[k], width)
                pygame.draw.line(s, (220, 225, 245), (cx - cam, cy), pts[-1], 4)
            self.draw_player(s, cam)
        for p in self.particles:
            pygame.draw.circle(s, p[5], (int(p[0] - cam), int(p[1])), 3)
        s.blit(font.render(f"Score: {self.score}   Distance: {int(self.px // TILE)}m", True, (0, 0, 0)), (10, 10))
        ct = font.render(f"Coins: {self.coins}", True, (0, 0, 0))
        s.blit(ct, (W - ct.get_width() - 10, 10))
        best = font.render(f"Best: {max(self.high_score, self.coins)}", True, (90, 60, 0))
        s.blit(best, (W - best.get_width() - 10, 38))
        if self.dead:
            ov = pygame.Surface((W, H), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 150))
            s.blit(ov, (0, 0))
            big = pygame.font.SysFont(None, 80)
            lines = [("GAME OVER", big, H // 2 - 90, (255, 255, 255)),
                     (self.death_reason, font, H // 2 - 20, (255, 255, 255)),
                     (f"Score: {self.score}   Coins: {self.coins}   Best: {self.high_score}", font, H // 2 + 10,
                      (255, 255, 255))]
            if self.new_record:
                lines.append(("NEW HIGH SCORE!", font, H // 2 + 45, (255, 215, 0)))
            lines.append(("Press R to restart", font, H // 2 + 85, (255, 255, 255)))
            for text, f, y, col in lines:
                t = f.render(text, True, col)
                s.blit(t, (W // 2 - t.get_width() // 2, y))


def main(max_frames=None):
    pygame.init()
    screen = pygame.display.set_mode((W, H), pygame.SCALED | pygame.RESIZABLE)
    pygame.display.set_caption("Coin Slasher")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont(None, 32)
    g = Game()
    frames = 0
    while True:
        jump_pressed = False
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                pygame.quit()
                return
            if e.type == pygame.KEYDOWN and e.key == pygame.K_SPACE:
                jump_pressed = True
            if e.type == pygame.KEYDOWN and e.key == pygame.K_F11:
                pygame.display.toggle_fullscreen()
            if e.type == pygame.KEYDOWN and e.key == pygame.K_r and g.dead:
                g.reset()
        g.update(pygame.key.get_pressed(), jump_pressed)
        g.draw(screen, font)
        pygame.display.flip()
        clock.tick(FPS)
        frames += 1
        if max_frames and frames >= max_frames:
            pygame.quit()
            return


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
