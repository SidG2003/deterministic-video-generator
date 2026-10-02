from manim import *
import numpy as np
import math
import random

random.seed(0)
np.random.seed(0)

PAL_BG = "#0b0f1a"
PAL_COLD = "#7aa2ff"
PAL_HOT = "#ff6b5e"
PAL_GOLD = "#ffd27a"
PAL_MINT = "#6fe3c2"
PAL_VIOLET = "#b58bff"
PAL_DIM = "#3a4568"


class Generated(ThreeDScene):
    def construct(self):
        self.camera.background_color = PAL_BG

        # ------------------------------------------------------------------
        # SECTION 0 — TITLE
        # ------------------------------------------------------------------
        self.title_moment()

        # ------------------------------------------------------------------
        # SECTION 1 — A GAS FINDS ITS ROOM (spreading = the arrow of time)
        # ------------------------------------------------------------------
        self.gas_spreads()

        # ------------------------------------------------------------------
        # SECTION 2 — MACROSTATE vs MICROSTATE (the core distinction)
        # ------------------------------------------------------------------
        self.micro_macro()

        # ------------------------------------------------------------------
        # SECTION 3 — COUNTING: why spread-out states are overwhelming
        # ------------------------------------------------------------------
        self.counting_binomial()

        # ------------------------------------------------------------------
        # SECTION 4 — BOLTZMANN: S = k ln W
        # ------------------------------------------------------------------
        self.boltzmann()

        # ------------------------------------------------------------------
        # SECTION 5 — THE SECOND LAW as a probability landscape
        # ------------------------------------------------------------------
        self.entropy_landscape()

        # ------------------------------------------------------------------
        # SECTION 6 — WORKED EXAMPLE: 4 coins / 4 particles
        # ------------------------------------------------------------------
        self.worked_example()

        # ------------------------------------------------------------------
        # SECTION 7 — CLOSING: the arrow of time
        # ------------------------------------------------------------------
        self.closing()

    # ======================================================================
    # helpers
    # ======================================================================
    def clear_all(self, run_time=1.0):
        mobs = [m for m in self.mobjects]
        if mobs:
            self.play(*[FadeOut(m) for m in mobs], run_time=run_time)
        self.stop_ambient_camera_rotation()

    def flat_camera(self):
        # look straight on for 2D-style sections
        self.set_camera_orientation(phi=0, theta=-90 * DEGREES)

    # ======================================================================
    # SECTION 0
    # ======================================================================
    def title_moment(self):
        self.flat_camera()

        # faint drifting particles behind the title for atmosphere
        dots = VGroup()
        for _ in range(60):
            p = np.array([
                random.uniform(-7, 7),
                random.uniform(-4, 4),
                0.0,
            ])
            d = Dot(p, radius=random.uniform(0.02, 0.06))
            d.set_color(random.choice([PAL_COLD, PAL_VIOLET, PAL_MINT]))
            d.set_opacity(random.uniform(0.15, 0.5))
            dots.add(d)
        self.add_fixed_in_frame_mobjects(dots)

        title = Text("ENTROPY", weight=BOLD).scale(1.5)
        title.set_color_by_gradient(PAL_COLD, PAL_VIOLET, PAL_HOT)
        subtitle = Text("and the Second Law of Thermodynamics", weight=LIGHT).scale(0.45)
        subtitle.set_color(PAL_GOLD)
        subtitle.next_to(title, DOWN, buff=0.4)
        tag = Text("why disorder wins — one atom at a time", slant=ITALIC).scale(0.32)
        tag.set_color("#9fb0d8")
        tag.next_to(subtitle, DOWN, buff=0.35)

        self.add_fixed_in_frame_mobjects(title, subtitle, tag)
        self.remove(title, subtitle, tag)

        self.play(
            LaggedStart(*[FadeIn(d, scale=1.5) for d in dots], lag_ratio=0.03),
            run_time=1.5,
        )
        self.play(Write(title), run_time=1.6)
        self.play(FadeIn(subtitle, shift=UP * 0.3), run_time=1.0)
        self.play(FadeIn(tag, shift=UP * 0.2), run_time=0.8)

        # gentle drift
        self.play(
            *[d.animate.shift(np.array([random.uniform(-0.3, 0.3),
                                        random.uniform(-0.3, 0.3), 0]))
              for d in dots],
            rate_func=there_and_back, run_time=2.0,
        )
        self.wait(0.6)
        self.play(FadeOut(title), FadeOut(subtitle), FadeOut(tag),
                  FadeOut(dots), run_time=1.2)

    # ======================================================================
    # SECTION 1 — gas released into a box
    # ======================================================================
    def gas_spreads(self):
        self.flat_camera()

        header = Text("Take away the partition...", weight=MEDIUM).scale(0.55).to_corner(UL)
        header.set_color(PAL_GOLD)
        self.add_fixed_in_frame_mobjects(header)
        self.play(FadeIn(header, shift=DOWN * 0.2), run_time=0.8)

        box = Rectangle(width=9.0, height=4.6, stroke_color=PAL_COLD, stroke_width=3)
        box.set_fill(PAL_DIM, 0.05)
        partition = DashedLine(box.get_top(), box.get_bottom(), color=PAL_GOLD, stroke_width=3)
        self.play(Create(box), Create(partition), run_time=1.2)

        # particles crammed into left half
        n = 55
        parts = VGroup()
        vel = []
        for _ in range(n):
            p = np.array([
                random.uniform(-4.3, -0.2),
                random.uniform(-2.1, 2.1),
                0.0,
            ])
            d = Dot(p, radius=0.07)
            d.set_color(PAL_HOT)
            d.set_sheen_factor(0.4)
            parts.add(d)
            ang = random.uniform(0, TAU)
            vel.append(np.array([np.cos(ang), np.sin(ang), 0.0]) * random.uniform(1.5, 3.0))
        self.play(LaggedStart(*[GrowFromCenter(d) for d in parts], lag_ratio=0.02),
                  run_time=1.4)
        self.wait(0.4)

        # remove partition
        self.play(FadeOut(partition), run_time=0.6)
        new_head = Text("...and the gas fills every corner", weight=MEDIUM).scale(0.55).to_corner(UL)
        new_head.set_color(PAL_GOLD)
        self.add_fixed_in_frame_mobjects(new_head)
        self.play(FadeTransform(header, new_head), run_time=0.8)

        # billiard-style diffusion using an updater
        bounds_x = (-4.4, 4.4)
        bounds_y = (-2.2, 2.2)

        def make_updater(idx):
            def upd(m, dt):
                v = vel[idx]
                pos = m.get_center() + v * dt
                if pos[0] < bounds_x[0] or pos[0] > bounds_x[1]:
                    v[0] *= -1
                    pos[0] = np.clip(pos[0], bounds_x[0], bounds_x[1])
                if pos[1] < bounds_y[0] or pos[1] > bounds_y[1]:
                    v[1] *= -1
                    pos[1] = np.clip(pos[1], bounds_y[0], bounds_y[1])
                m.move_to(pos)
            return upd

        for i, d in enumerate(parts):
            d.add_updater(make_updater(i))

        # gradual heating toward equilibrium colour
        self.wait(4.5)
        self.play(parts.animate.set_color(PAL_COLD), run_time=1.5)

        for d in parts:
            d.clear_updaters()

        caption = Text("It never spontaneously crowds back into one half.",
                       slant=ITALIC).scale(0.4)
        caption.set_color("#9fb0d8").to_edge(DOWN, buff=0.5)
        self.add_fixed_in_frame_mobjects(caption)
        self.play(FadeIn(caption, shift=UP * 0.2), run_time=0.9)
        self.wait(1.2)

        self.play(FadeOut(box), FadeOut(parts), FadeOut(new_head),
                  FadeOut(caption), run_time=1.2)

    # ======================================================================
    # SECTION 2 — macrostate vs microstate
    # ======================================================================
    def micro_macro(self):
        self.flat_camera()

        header = Text("Microstate vs. Macrostate", weight=BOLD).scale(0.6).to_corner(UL)
        header.set_color(PAL_MINT)
        self.add_fixed_in_frame_mobjects(header)
        self.play(FadeIn(header, shift=DOWN * 0.2), run_time=0.8)

        # left panel: microstate (exact positions)
        left_box = Rectangle(width=5.0, height=4.0, stroke_color=PAL_COLD, stroke_width=2)
        left_box.move_to(LEFT * 3.4 + DOWN * 0.3)
        right_box = left_box.copy().set_stroke(PAL_VIOLET).move_to(RIGHT * 3.4 + DOWN * 0.3)

        micro_lbl = Text("MICROSTATE", weight=BOLD).scale(0.4).next_to(left_box, UP, buff=0.2)
        micro_lbl.set_color(PAL_COLD)
        micro_sub = Text("every atom's exact position & velocity", slant=ITALIC).scale(0.26)
        micro_sub.set_color("#9fb0d8").next_to(left_box, DOWN, buff=0.2)

        macro_lbl = Text("MACROSTATE", weight=BOLD).scale(0.4).next_to(right_box, UP, buff=0.2)
        macro_lbl.set_color(PAL_VIOLET)
        macro_sub = Text("what we can measure: T, P, V", slant=ITALIC).scale(0.26)
        macro_sub.set_color("#9fb0d8").next_to(right_box, DOWN, buff=0.2)

        self.play(Create(left_box), Create(right_box),
                  FadeIn(micro_lbl), FadeIn(macro_lbl),
                  FadeIn(micro_sub), FadeIn(macro_sub), run_time=1.2)

        # microstate particles with velocity arrows
        micro_parts = VGroup()
        for _ in range(14):
            p = left_box.get_center() + np.array([
                random.uniform(-2.1, 2.1), random.uniform(-1.6, 1.6), 0])
            d = Dot(p, radius=0.08, color=PAL_HOT)
            ang = random.uniform(0, TAU)
            arr = Arrow(p, p + np.array([np.cos(ang), np.sin(ang), 0]) * 0.5,
                        buff=0, stroke_width=3, color=PAL_GOLD,
                        max_tip_length_to_length_ratio=0.4)
            micro_parts.add(VGroup(d, arr))
        self.play(LaggedStart(*[FadeIn(m, scale=1.3) for m in micro_parts],
                              lag_ratio=0.06), run_time=1.6)

        # macrostate: same box but summarised by a few numbers / bulk glow
        glow = right_box.copy().set_stroke(width=0).set_fill(PAL_HOT, 0.18)
        macro_vals = VGroup(
            MathTex(r"T = 300\,\text{K}", color=PAL_HOT),
            MathTex(r"P = 1\,\text{atm}", color=PAL_GOLD),
            MathTex(r"V = 1\,\text{L}", color=PAL_MINT),
        ).scale(0.55).arrange(DOWN, buff=0.4).move_to(right_box.get_center())
        self.play(FadeIn(glow), LaggedStart(*[Write(v) for v in macro_vals],
                  lag_ratio=0.3), run_time=1.8)
        self.wait(0.6)

        # key idea: MANY microstates -> ONE macrostate
        arrow = Arrow(left_box.get_right(), right_box.get_left(),
                      buff=0.15, color="#ffffff", stroke_width=4)
        many = Text("many", weight=BOLD).scale(0.35).set_color(PAL_COLD)
        one = Text("one", weight=BOLD).scale(0.35).set_color(PAL_VIOLET)
        many.next_to(arrow, UP, buff=0.1).shift(LEFT * 0.9)
        one.next_to(arrow, UP, buff=0.1).shift(RIGHT * 0.9)
        self.play(GrowArrow(arrow), FadeIn(many), FadeIn(one), run_time=1.0)

        punch = Text("Countless microstates look identical from the outside.",
                     slant=ITALIC).scale(0.4).set_color(PAL_GOLD).to_edge(DOWN, buff=0.35)
        self.add_fixed_in_frame_mobjects(punch)
        self.play(FadeIn(punch, shift=UP * 0.2), run_time=0.9)
        self.wait(1.3)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=1.1)

    # ======================================================================
    # SECTION 3 — counting microstates (binomial distribution)
    # ======================================================================
    def counting_binomial(self):
        self.flat_camera()

        header = Text("Counting the possibilities", weight=BOLD).scale(0.58).to_corner(UL)
        header.set_color(PAL_GOLD)
        self.add_fixed_in_frame_mobjects(header)
        self.play(FadeIn(header, shift=DOWN * 0.2), run_time=0.8)

        prompt = Text("Left or right? For N particles: how many ways land k on the left?",
                      slant=ITALIC).scale(0.34).set_color("#9fb0d8")
        prompt.next_to(header, DOWN, buff=0.1).to_edge(LEFT, buff=0.5)
        self.add_fixed_in_frame_mobjects(prompt)
        self.play(FadeIn(prompt), run_time=0.7)

        # histogram of C(N,k) for N particles
        N = 20
        coeffs = [math.comb(N, k) for k in range(N + 1)]
        cmax = max(coeffs)

        axes = Axes(
            x_range=[0, N, 5],
            y_range=[0, cmax * 1.1, cmax / 4],
            x_length=10.0,
            y_length=4.6,
            tips=False,
            axis_config={"color": PAL_DIM, "include_numbers": False},
        ).shift(DOWN * 0.4)
        x_lbl = Text("# on the left", weight=LIGHT).scale(0.35).set_color(PAL_COLD)
        x_lbl.next_to(axes.x_axis, DOWN, buff=0.25)
        y_lbl = Text("# of microstates  W", weight=LIGHT).scale(0.33).set_color(PAL_VIOLET)
        y_lbl.rotate(PI / 2).next_to(axes.y_axis, LEFT, buff=0.2)
        self.add_fixed_in_frame_mobjects(axes, x_lbl, y_lbl)
        self.play(Create(axes), FadeIn(x_lbl), FadeIn(y_lbl), run_time=1.2)

        bars = VGroup()
        bw = axes.c2p(1, 0)[0] - axes.c2p(0, 0)[0]
        for k, c in enumerate(coeffs):
            base = axes.c2p(k, 0)
            top = axes.c2p(k, c)
            h = top[1] - base[1]
            bar = Rectangle(width=bw * 0.8, height=max(h, 0.001),
                            stroke_width=0)
            bar.move_to(base, aligned_edge=DOWN)
            # colour: extremes (ordered) red-dim, centre (spread) bright
            frac = abs(k - N / 2) / (N / 2)
            bar.set_fill(interpolate_color(
                ManimColor(PAL_MINT), ManimColor(PAL_HOT), frac), 0.9)
            bars.add(bar)
        self.add_fixed_in_frame_mobjects(bars)
        self.play(LaggedStart(*[GrowFromEdge(b, DOWN) for b in bars],
                              lag_ratio=0.05), run_time=2.2)

        # highlight the extremes vs the peak
        peak_brace = Brace(bars[N // 2], UP)
        peak_txt = Text("even spread:\noverwhelmingly likely", weight=MEDIUM).scale(0.3)
        peak_txt.set_color(PAL_HOT).next_to(peak_brace, UP, buff=0.1)
        self.add_fixed_in_frame_mobjects(peak_brace, peak_txt)
        self.play(GrowFromCenter(peak_brace), FadeIn(peak_txt), run_time=0.9)

        edge_arrow = Arrow(bars[0].get_top() + UP * 1.2, bars[0].get_top() + UP * 0.1,
                           color=PAL_MINT, buff=0.05, stroke_width=3)
        edge_txt = Text("all on one side:\nvanishingly rare", weight=MEDIUM).scale(0.28)
        edge_txt.set_color(PAL_MINT).next_to(edge_arrow, UP, buff=0.05)
        self.add_fixed_in_frame_mobjects(edge_arrow, edge_txt)
        self.play(GrowArrow(edge_arrow), FadeIn(edge_txt), run_time=0.9)
        self.wait(0.8)

        ratio = MathTex(
            r"\frac{W_{\text{all left}}}{W_{\text{even}}}=\frac{1}{\binom{20}{10}}"
            r"\approx \frac{1}{184{,}756}",
            color=PAL_GOLD).scale(0.6).to_edge(DOWN, buff=0.4)
        self.add_fixed_in_frame_mobjects(ratio)
        self.play(Write(ratio), run_time=1.4)
        self.wait(1.0)

        big = Text("...and real gases have ~10²³ particles.",
                   weight=MEDIUM).scale(0.42).set_color(PAL_VIOLET)
        big.move_to(ratio)
        self.play(FadeTransform(ratio, big), run_time=1.0)
        self.wait(1.3)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=1.1)

    # ======================================================================
    # SECTION 4 — Boltzmann S = k ln W
    # ======================================================================
    def boltzmann(self):
        self.flat_camera()

        header = Text("Boltzmann's insight", weight=BOLD).scale(0.6).to_corner(UL)
        header.set_color(PAL_MINT)
        self.add_fixed_in_frame_mobjects(header)
        self.play(FadeIn(header, shift=DOWN * 0.2), run_time=0.8)

        eq = MathTex("S", "=", "k", r"\ln", "W").scale(1.6)
        eq[0].set_color(PAL_HOT)
        eq[2].set_color(PAL_GOLD)
        eq[4].set_color(PAL_VIOLET)
        eq.move_to(UP * 0.6)
        self.add_fixed_in_frame_mobjects(eq)
        self.play(Write(eq), run_time=1.6)

        labels = VGroup(
            Text("entropy", color=PAL_HOT).scale(0.4),
            Text("Boltzmann's constant", color=PAL_GOLD).scale(0.4),
            Text("number of microstates", color=PAL_VIOLET).scale(0.4),
        )
        labels[0].next_to(eq[0], DOWN, buff=1.1)
        labels[1].next_to(eq[2], DOWN, buff=1.6)
        labels[2].next_to(eq[4], DOWN, buff=1.1)
        arrs = VGroup(
            Arrow(labels[0].get_top(), eq[0].get_bottom(), buff=0.1,
                  color=PAL_HOT, stroke_width=2.5),
            Arrow(labels[1].get_top(), eq[2].get_bottom(), buff=0.1,
                  color=PAL_GOLD, stroke_width=2.5),
            Arrow(labels[2].get_top(), eq[4].get_bottom(), buff=0.1,
                  color=PAL_VIOLET, stroke_width=2.5),
        )
        self.add_fixed_in_frame_mobjects(labels, arrs)
        self.play(LaggedStart(
            *[AnimationGroup(GrowArrow(a), FadeIn(l))
              for a, l in zip(arrs, labels)], lag_ratio=0.3), run_time=1.8)
        self.wait(0.8)

        why = Text("Why the logarithm? Because entropy should ADD.",
                   weight=MEDIUM).scale(0.4).set_color(PAL_GOLD)
        why.to_edge(DOWN, buff=1.1)
        self.add_fixed_in_frame_mobjects(why)
        self.play(FadeIn(why, shift=UP * 0.2), run_time=0.8)

        add_eq = MathTex(
            r"W_{1+2}=W_1\,W_2 \;\Rightarrow\; S_{1+2}=k\ln(W_1 W_2)=S_1+S_2",
            color="#cfe0ff").scale(0.55).to_edge(DOWN, buff=0.4)
        self.add_fixed_in_frame_mobjects(add_eq)
        self.play(Write(add_eq), run_time=1.8)
        self.wait(1.4)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=1.1)

    # ======================================================================
    # SECTION 5 — probability landscape (3D) : the second law
    # ======================================================================
    def entropy_landscape(self):
        self.set_camera_orientation(phi=62 * DEGREES, theta=-50 * DEGREES)

        title = Text("The Second Law", weight=BOLD).scale(0.65).to_corner(UL)
        title.set_color(PAL_HOT)
        sub = Text("systems roll toward the widest valley of possibilities",
                   slant=ITALIC).scale(0.3).set_color("#9fb0d8")
        sub.next_to(title, DOWN, buff=0.12).to_edge(LEFT, buff=0.5)
        self.add_fixed_in_frame_mobjects(title, sub)
        self.play(FadeIn(title, shift=DOWN * 0.2), FadeIn(sub), run_time=0.9)

        axes = ThreeDAxes(
            x_range=[-4, 4, 2], y_range=[-4, 4, 2], z_range=[0, 4, 1],
            x_length=8, y_length=8, z_length=3.5,
        )
        axes.set_color(PAL_DIM)

        # an entropy surface: a broad basin (high entropy) with a rugged edge
        def surf_func(u, v):
            r2 = u * u + v * v
            z = 2.6 * np.exp(-0.10 * r2) + 0.15 * np.sin(1.5 * u) * np.cos(1.5 * v)
            return np.array([u, v, z])

        surface = Surface(
            surf_func,
            u_range=[-4, 4], v_range=[-4, 4], resolution=(40, 40),
            fill_opacity=0.65, stroke_width=0.5,
        )
        surface.set_fill_by_value(
            axes=axes,
            colorscale=[(PAL_VIOLET, 0.0), (PAL_COLD, 1.2),
                        (PAL_MINT, 2.0), (PAL_HOT, 2.8)],
            axis=2,
        )
        surface.set_stroke("#26314d", 0.5, 0.4)

        self.play(Create(surface, lag_ratio=0.01), run_time=3.0)
        self.begin_ambient_camera_rotation(rate=0.12)

        # a marble that rolls up the entropy hill (toward the summit basin)
        t = ValueTracker(0.0)

        def marble_path(tt):
            # spiral inward from the rim to the high-entropy summit
            r = 3.6 * (1 - tt)
            ang = 6 * tt
            x = r * np.cos(ang)
            y = r * np.sin(ang)
            z = surf_func(x, y)[2]
            return np.array([x, y, z + 0.12])

        marble = always_redraw(lambda: Dot3D(
            point=marble_path(t.get_value()), radius=0.14, color=PAL_GOLD))
        trail = TracedPath(lambda: marble_path(t.get_value()),
                           stroke_color=PAL_GOLD, stroke_width=4, stroke_opacity=0.8)
        self.add(trail, marble)

        low_tag = Text("low entropy", weight=MEDIUM).scale(0.35).set_color(PAL_VIOLET)
        high_tag = Text("maximum entropy", weight=MEDIUM).scale(0.35).set_color(PAL_HOT)
        low_tag.to_corner(DR)
        high_tag.next_to(low_tag, UP, buff=0.2).to_edge(RIGHT, buff=0.5)
        self.add_fixed_in_frame_mobjects(low_tag, high_tag)
        self.play(FadeIn(low_tag), FadeIn(high_tag), run_time=0.7)

        self.play(t.animate.set_value(1.0), run_time=6.0, rate_func=rush_from)
        self.wait(0.8)

        law = MathTex(r"\Delta S_{\text{universe}} \geq 0", color=PAL_GOLD).scale(1.0)
        law.to_edge(DOWN, buff=0.5)
        self.add_fixed_in_frame_mobjects(law)
        self.play(Write(law), run_time=1.4)
        self.wait(1.4)

        self.stop_ambient_camera_rotation()
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=1.2)

    # ======================================================================
    # SECTION 6 — worked example: 4 particles / 4 coins
    # ======================================================================
    def worked_example(self):
        self.flat_camera()

        header = Text("Worked example: 4 particles", weight=BOLD).scale(0.55).to_corner(UL)
        header.set_color(PAL_GOLD)
        self.add_fixed_in_frame_mobjects(header)
        self.play(FadeIn(header, shift=DOWN * 0.2), run_time=0.8)

        note = Text("Each particle is independently on the Left or Right (2⁴ = 16 microstates)",
                    slant=ITALIC).scale(0.32).set_color("#9fb0d8")
        note.next_to(header, DOWN, buff=0.1).to_edge(LEFT, buff=0.5)
        self.add_fixed_in_frame_mobjects(note)
        self.play(FadeIn(note), run_time=0.7)

        # columns for macrostates k = number on left = 0..4
        counts = [math.comb(4, k) for k in range(5)]  # 1,4,6,4,1
        col_x = [-5.2, -2.6, 0.0, 2.6, 5.2]
        groups = VGroup()
        label_grp = VGroup()

        for k in range(5):
            top = UP * 1.9
            # small icon boxes representing L|R for each of the W microstates
            micro_col = VGroup()
            for j in range(counts[k]):
                mini = self.mini_box(k)
                micro_col.add(mini)
            micro_col.arrange(DOWN, buff=0.18)
            micro_col.move_to(np.array([col_x[k], 0.0, 0.0]) + DOWN * 0.5)
            groups.add(micro_col)

            lab = VGroup(
                Text(f"k={k}", weight=BOLD).scale(0.4).set_color(PAL_COLD),
                MathTex(f"W={counts[k]}", color=PAL_HOT).scale(0.5),
            ).arrange(DOWN, buff=0.12)
            lab.move_to(np.array([col_x[k], 2.4, 0.0]))
            label_grp.add(lab)

        self.add_fixed_in_frame_mobjects(label_grp, groups)
        self.play(LaggedStart(*[FadeIn(l, shift=DOWN * 0.2) for l in label_grp],
                              lag_ratio=0.15), run_time=1.2)
        self.play(LaggedStart(
            *[GrowFromCenter(m) for col in groups for m in col],
            lag_ratio=0.03), run_time=2.4)
        self.wait(0.6)

        # highlight the even-split macrostate as most probable
        rect = SurroundingRectangle(VGroup(label_grp[2], groups[2]),
                                    color=PAL_GOLD, buff=0.2)
        self.add_fixed_in_frame_mobjects(rect)
        self.play(Create(rect), run_time=0.9)
        prob = MathTex(r"P(\text{even split})=\frac{6}{16}=37.5\%",
                       color=PAL_GOLD).scale(0.6).to_edge(DOWN, buff=0.35)
        prob2 = MathTex(r"P(\text{all left})=\frac{1}{16}=6.25\%",
                        color=PAL_MINT).scale(0.55)
        prob.to_edge(DOWN, buff=0.7)
        prob2.next_to(prob, DOWN, buff=0.15)
        self.add_fixed_in_frame_mobjects(prob, prob2)
        self.play(Write(prob), run_time=1.0)
        self.play(Write(prob2), run_time=1.0)
        self.wait(1.4)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=1.1)

    def mini_box(self, k_left):
        # a tiny 2-cell box, k_left dots on the left cell chosen visually
        box = RoundedRectangle(width=0.9, height=0.34, corner_radius=0.06,
                               stroke_width=1.5, stroke_color=PAL_DIM)
        divider = Line(box.get_top(), box.get_bottom(), stroke_width=1,
                       color=PAL_DIM).move_to(box.get_center())
        # colour intensity by how balanced the split is
        frac = abs(k_left - 2) / 2
        box.set_fill(interpolate_color(ManimColor(PAL_HOT),
                                       ManimColor(PAL_MINT), frac), 0.35)
        return VGroup(box, divider)

    # ======================================================================
    # SECTION 7 — closing: arrow of time
    # ======================================================================
    def closing(self):
        self.flat_camera()

        # a timeline arrow made of scattering particles
        arrow = Arrow(LEFT * 6, RIGHT * 6, buff=0, color="#ffffff", stroke_width=3)
        arrow.shift(DOWN * 2.5)
        t_lbl = Text("time", weight=LIGHT).scale(0.4).set_color("#9fb0d8")
        t_lbl.next_to(arrow.get_end(), UP, buff=0.15)
        self.add_fixed_in_frame_mobjects(arrow, t_lbl)
        self.play(GrowArrow(arrow), FadeIn(t_lbl), run_time=1.0)

        # ordered cluster on the left morphing to spread cloud on the right
        cluster = VGroup()
        for _ in range(45):
            p = np.array([random.uniform(-5.7, -4.7),
                          random.uniform(-0.6, 0.6), 0])
            cluster.add(Dot(p, radius=0.06, color=PAL_VIOLET))
        self.add_fixed_in_frame_mobjects(cluster)
        self.play(LaggedStart(*[FadeIn(d, scale=1.4) for d in cluster],
                              lag_ratio=0.02), run_time=1.2)

        targets = []
        for d in cluster:
            tp = np.array([random.uniform(0.5, 5.7),
                           random.uniform(-1.6, 1.6), 0])
            targets.append(tp)
        self.play(
            *[d.animate.move_to(tp).set_color(PAL_HOT)
              for d, tp in zip(cluster, targets)],
            run_time=2.5, rate_func=smooth,
        )

        final = Text("The arrow of time points toward more likely arrangements.",
                     weight=MEDIUM).scale(0.44).set_color(PAL_GOLD)
        final.move_to(UP * 1.2)
        final2 = Text("Entropy is nature keeping score.", slant=ITALIC).scale(0.5)
        final2.set_color_by_gradient(PAL_COLD, PAL_VIOLET, PAL_HOT)
        final2.next_to(final, DOWN, buff=0.4)
        self.add_fixed_in_frame_mobjects(final, final2)
        self.play(Write(final), run_time=1.4)
        self.play(FadeIn(final2, scale=1.1), run_time=1.2)
        self.wait(1.6)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=1.6)
        self.wait(0.4)