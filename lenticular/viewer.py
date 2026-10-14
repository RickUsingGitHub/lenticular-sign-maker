"""Lightweight OpenGL 3D viewer (QOpenGLWidget + PyOpenGL, GLSL 1.20)."""

from __future__ import annotations

import ctypes
import math

import numpy as np
from OpenGL import GL as gl
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtOpenGLWidgets import QOpenGLWidget

VERT_SRC = """
#version 120
attribute vec3 a_pos;
attribute vec3 a_nrm;
uniform mat4 u_model;
uniform mat4 u_view;
uniform mat4 u_proj;
varying vec3 v_nrm;
varying vec3 v_pos;
void main() {
    vec4 vp = u_view * (u_model * vec4(a_pos, 1.0));
    v_pos = vp.xyz;
    v_nrm = mat3(u_view) * mat3(u_model) * a_nrm;
    gl_Position = u_proj * vp;
}
"""

FRAG_SRC = """
#version 120
uniform vec3 u_color;
uniform int u_mode;          // 0 unlit, 1 lit, 2 background gradient
uniform vec3 u_bg_top;
uniform vec3 u_bg_bot;
uniform float u_vh;
varying vec3 v_nrm;
varying vec3 v_pos;
void main() {
    if (u_mode == 2) {
        float t = clamp(gl_FragCoord.y / u_vh, 0.0, 1.0);
        gl_FragColor = vec4(mix(u_bg_bot, u_bg_top, t), 1.0);
        return;
    }
    if (u_mode == 0) { gl_FragColor = vec4(u_color, 1.0); return; }
    vec3 n = normalize(v_nrm);
    vec3 vdir = normalize(-v_pos);
    // soft, mostly even light: the colours matter more here than the shape
    vec3 l1 = normalize(vec3(0.35, 0.55, 0.75));
    vec3 l2 = normalize(vec3(-0.7, -0.25, 0.45));
    float dif = max(dot(n, l1), 0.0) * 0.30 + max(dot(n, l2), 0.0) * 0.12;
    vec3 h = normalize(l1 + vdir);
    float spec = pow(max(dot(n, h), 0.0), 40.0) * 0.06;
    gl_FragColor = vec4(u_color * (0.60 + dif) + vec3(spec), 1.0);
}
"""


def _perspective(fovy_deg, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fovy_deg) / 2.0)
    m = np.zeros((4, 4))
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2.0 * far * near / (near - far)
    m[3, 2] = -1.0
    return m


def _look_at(eye, target, up):
    f = target - eye
    f = f / np.linalg.norm(f)
    s = np.cross(f, up)
    s = s / np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.identity(4)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[0, 3], m[1, 3], m[2, 3] = -s @ eye, -u @ eye, f @ eye
    return m


def standing_transform(turn_deg=0.0, lift=0.0):
    """4x4 model matrix that stands a flat-printed sign up to be looked at.

    The sign's face (print +z) is turned towards -y, where the camera starts,
    with its top edge (print +y) up.  ``lift`` is the height of the sign's
    middle above its back, so it ends up centred on the origin.  ``turn_deg``
    then turns it in its own plane - 180 is upside down.
    """
    stand = np.identity(4)
    stand[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    stand[:3, 3] = (0.0, lift, 0.0)
    a = math.radians(turn_deg)
    c, s = math.cos(a), math.sin(a)
    turn = np.identity(4)                     # about the y axis: the sign's own normal
    turn[0, 0], turn[0, 2], turn[2, 0], turn[2, 2] = c, s, -s, c
    return turn @ stand


class _Mesh:
    def __init__(self, verts, faces, color):
        tri = verts[faces].astype(np.float32)                     # (m,3,3)
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        ln = np.linalg.norm(n, axis=1, keepdims=True)
        ln[ln == 0] = 1
        data = np.empty((len(faces), 3, 6), dtype=np.float32)     # flat shaded: all edges are crisp
        data[:, :, :3] = tri
        data[:, :, 3:] = (n / ln)[:, None, :]
        self.data = data.reshape(-1, 6)
        self.count = len(self.data)
        self.lo = verts.min(axis=0)
        self.hi = verts.max(axis=0)
        self.color = color
        self.vbo = None


class Viewer3D(QOpenGLWidget):
    BG_TOP = (0.20, 0.23, 0.28)
    BG_BOT = (0.08, 0.09, 0.11)

    dragged = Signal()                # the user moved the camera
    reset_requested = Signal()        # double-click or F: back to the chosen view
    resized = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.meshes: dict[str, _Mesh] = {}
        self._stale_vbos: list[int] = []
        self.prog = None
        self.grid_vbo = None
        self.grid_count = 0
        self.bed = (256.0, 256.0)     # printer bed drawn under the parts (mm)
        self.show_bed = True
        self._grid_bed = None         # bed size the grid buffer was built for
        self.model = np.identity(4)   # one placement for every mesh
        self.fov = 30.0
        self.yaw = -60.0
        self.pitch = 22.0
        self.dist = 200.0
        self.target = np.array([0.0, 0.0, 0.0])
        self._last = None
        self._gl_error = None
        self.setMinimumSize(320, 240)
        self.setFocusPolicy(Qt.StrongFocus)

    # ------------------------------------------------------------ scene API
    def set_mesh(self, name, verts, faces, color):
        old = self.meshes.get(name)
        if old is not None and old.vbo is not None:
            self._stale_vbos.append(old.vbo)
        self.meshes[name] = _Mesh(verts, faces, color)
        self.update()

    def remove_mesh(self, name):
        old = self.meshes.pop(name, None)
        if old is not None and old.vbo is not None:
            self._stale_vbos.append(old.vbo)
        self.update()

    def set_model(self, model):
        self.model = model
        self.update()

    def set_bed(self, width: float, depth: float):
        """Size of the printer bed shown under the sign."""
        bed = (float(width), float(depth))
        if bed != self.bed:
            self.bed = bed
            self.update()

    def scene_bounds(self):
        lo = np.array([np.inf] * 3)
        hi = -lo
        for m in self.meshes.values():
            corners = np.array([[x, y, z] for x in (m.lo[0], m.hi[0])
                                for y in (m.lo[1], m.hi[1]) for z in (m.lo[2], m.hi[2])])
            pts = corners @ self.model[:3, :3].T + self.model[:3, 3]
            lo = np.minimum(lo, pts.min(0))
            hi = np.maximum(hi, pts.max(0))
        if not np.isfinite(lo).all():
            return np.zeros(3), np.ones(3) * 50
        return lo, hi

    def fit_distance(self):
        """Camera distance that shows the whole scene, whatever way it is turned."""
        lo, hi = self.scene_bounds()
        radius = max(np.linalg.norm(hi - lo) / 2.0, 10.0)
        aspect = max(self.width(), 1) / max(self.height(), 1)
        fov = math.radians(self.fov) * min(1.0, aspect)
        return radius / math.sin(fov / 2.0) * 1.05

    def fit_view(self):
        lo, hi = self.scene_bounds()
        self.target = (lo + hi) / 2.0
        self.dist = self.fit_distance()
        self.update()

    # ---------------------------------------------------------------- GL
    def initializeGL(self):
        for m in self.meshes.values():
            m.vbo = None
        self._stale_vbos.clear()
        self.grid_vbo = None
        self._grid_bed = None
        try:
            self.prog = self._build_program()
            self._quad_vbo = gl.glGenBuffers(1)
            quad = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0],
                             [-1, -1, 0], [1, 1, 0], [-1, 1, 0]], dtype=np.float32)
            gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._quad_vbo)
            gl.glBufferData(gl.GL_ARRAY_BUFFER, quad.nbytes, quad, gl.GL_STATIC_DRAW)
            gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)
            self._gl_error = None
        except Exception as exc:  # pragma: no cover - driver specific
            self.prog = None
            self._gl_error = str(exc)

    def _build_program(self):
        def compile_shader(src, kind):
            sh = gl.glCreateShader(kind)
            gl.glShaderSource(sh, src)
            gl.glCompileShader(sh)
            if not gl.glGetShaderiv(sh, gl.GL_COMPILE_STATUS):
                raise RuntimeError(gl.glGetShaderInfoLog(sh).decode(errors="replace"))
            return sh

        vs = compile_shader(VERT_SRC, gl.GL_VERTEX_SHADER)
        fs = compile_shader(FRAG_SRC, gl.GL_FRAGMENT_SHADER)
        prog = gl.glCreateProgram()
        gl.glAttachShader(prog, vs)
        gl.glAttachShader(prog, fs)
        gl.glBindAttribLocation(prog, 0, "a_pos")
        gl.glBindAttribLocation(prog, 1, "a_nrm")
        gl.glLinkProgram(prog)
        if not gl.glGetProgramiv(prog, gl.GL_LINK_STATUS):
            raise RuntimeError(gl.glGetProgramInfoLog(prog).decode(errors="replace"))
        gl.glDeleteShader(vs)
        gl.glDeleteShader(fs)
        self._loc = {n: gl.glGetUniformLocation(prog, n) for n in (
            "u_model", "u_view", "u_proj", "u_color", "u_mode", "u_bg_top", "u_bg_bot", "u_vh")}
        return prog

    def _upload(self, m: _Mesh):
        m.vbo = gl.glGenBuffers(1)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, m.vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, m.data.nbytes, m.data, gl.GL_STATIC_DRAW)

    def _ensure_grid(self):
        """The printer bed: a 10 mm grid of its real size, with an outline."""
        if self.grid_vbo is not None and self._grid_bed == self.bed:
            return
        if self.grid_vbo is not None:
            gl.glDeleteBuffers(1, [self.grid_vbo])
        bx, by = self.bed
        x0, y0, x1, y1 = -bx / 2.0, -by / 2.0, bx / 2.0, by / 2.0
        lines = []
        for x in x0 + np.arange(0.0, bx + 1e-6, 10.0):
            lines += [(x, y0, 0), (x, y1, 0)]
        for y in y0 + np.arange(0.0, by + 1e-6, 10.0):
            lines += [(x0, y, 0), (x1, y, 0)]
        self.grid_count = len(lines)
        lines += [(x0, y0, 0), (x1, y0, 0), (x1, y0, 0), (x1, y1, 0),      # bed outline
                  (x1, y1, 0), (x0, y1, 0), (x0, y1, 0), (x0, y0, 0)]
        arr = np.array(lines, dtype=np.float32)
        self.grid_vbo = gl.glGenBuffers(1)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self.grid_vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, arr.nbytes, arr, gl.GL_STATIC_DRAW)
        self._grid_bed = self.bed

    def _mat(self, name, m):
        gl.glUniformMatrix4fv(self._loc[name], 1, gl.GL_TRUE, m.astype(np.float32))

    def view_matrix(self):
        y, p = math.radians(self.yaw), math.radians(self.pitch)
        off = np.array([math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), math.sin(p)])
        eye = self.target + off * self.dist
        return _look_at(eye, self.target, np.array([0.0, 0.0, 1.0]))

    def resizeGL(self, w, h):
        self.resized.emit()

    def paintGL(self):
        if self.prog is None:
            gl.glClearColor(0.1, 0.1, 0.1, 1)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            return
        try:
            self._paint()
        except Exception as exc:  # pragma: no cover
            self._gl_error = str(exc)

    def _paint(self):
        if self._stale_vbos:
            gl.glDeleteBuffers(len(self._stale_vbos), self._stale_vbos)
            self._stale_vbos.clear()
        w, h = max(self.width(), 1), max(self.height(), 1)
        dpr = self.devicePixelRatioF()
        gl.glClearColor(*self.BG_BOT, 1.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
        gl.glUseProgram(self.prog)
        L = self._loc

        # background gradient
        gl.glDisable(gl.GL_DEPTH_TEST)
        gl.glDisable(gl.GL_CULL_FACE)
        eye4 = np.identity(4)
        self._mat("u_model", eye4)
        self._mat("u_view", eye4)
        self._mat("u_proj", eye4)
        gl.glUniform1i(L["u_mode"], 2)
        gl.glUniform3f(L["u_bg_top"], *self.BG_TOP)
        gl.glUniform3f(L["u_bg_bot"], *self.BG_BOT)
        gl.glUniform1f(L["u_vh"], h * dpr)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._quad_vbo)
        gl.glEnableVertexAttribArray(0)
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, gl.GL_FALSE, 12, ctypes.c_void_p(0))
        gl.glDisableVertexAttribArray(1)
        gl.glVertexAttrib3f(1, 0.0, 0.0, 1.0)
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)

        gl.glEnable(gl.GL_DEPTH_TEST)
        gl.glDepthFunc(gl.GL_LEQUAL)
        lo, hi = self.scene_bounds()
        size = float(np.linalg.norm(hi - lo))
        reach = size + (math.hypot(*self.bed) if self.show_bed else 0.0)
        # a tight depth range: the ridges are small next to the viewing distance
        near = max(self.dist - reach, self.dist * 0.02, 0.5)
        proj = _perspective(self.fov, w / h, near, self.dist + reach + 10.0)
        self._mat("u_view", self.view_matrix())
        self._mat("u_proj", proj)

        if self.show_bed:                 # printer bed: grid, then a brighter outline
            self._ensure_grid()
            self._mat("u_model", eye4)
            gl.glUniform1i(L["u_mode"], 0)
            gl.glUniform3f(L["u_color"], 0.27, 0.30, 0.35)
            gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self.grid_vbo)
            gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, gl.GL_FALSE, 12, ctypes.c_void_p(0))
            gl.glDrawArrays(gl.GL_LINES, 0, self.grid_count)
            gl.glUniform3f(L["u_color"], 0.50, 0.56, 0.64)
            gl.glDrawArrays(gl.GL_LINES, self.grid_count, 8)

        gl.glUniform1i(L["u_mode"], 1)
        gl.glEnableVertexAttribArray(1)
        gl.glEnable(gl.GL_CULL_FACE)
        gl.glCullFace(gl.GL_BACK)
        self._mat("u_model", self.model)
        for m in self.meshes.values():
            if m.vbo is None:
                self._upload(m)
            gl.glUniform3f(L["u_color"], *m.color)
            gl.glBindBuffer(gl.GL_ARRAY_BUFFER, m.vbo)
            gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, gl.GL_FALSE, 24, ctypes.c_void_p(0))
            gl.glVertexAttribPointer(1, 3, gl.GL_FLOAT, gl.GL_FALSE, 24, ctypes.c_void_p(12))
            gl.glDrawArrays(gl.GL_TRIANGLES, 0, m.count)
        gl.glDisable(gl.GL_CULL_FACE)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)
        gl.glUseProgram(0)

    # ------------------------------------------------------------- mouse
    def mousePressEvent(self, ev):
        self._last = ev.position()

    def mouseMoveEvent(self, ev):
        if self._last is None:
            self._last = ev.position()
            return
        d: QPointF = ev.position() - self._last
        self._last = ev.position()
        if ev.buttons() & Qt.LeftButton and not (ev.modifiers() & Qt.ShiftModifier):
            self.yaw -= d.x() * 0.3
            self.pitch = max(-89.0, min(89.0, self.pitch + d.y() * 0.3))
        elif ev.buttons() & (Qt.RightButton | Qt.MiddleButton | Qt.LeftButton):
            v = self.view_matrix()
            right, up = v[0, :3], v[1, :3]
            k = self.dist * 2.0 * math.tan(math.radians(self.fov) / 2.0) / max(self.height(), 1)
            self.target = self.target - right * d.x() * k + up * d.y() * k
        self.dragged.emit()
        self.update()

    def mouseReleaseEvent(self, ev):
        self._last = None

    def mouseDoubleClickEvent(self, ev):
        self.reset_requested.emit()

    def wheelEvent(self, ev):
        steps = ev.angleDelta().y() / 120.0
        self.dist = max(5.0, min(20000.0, self.dist * (0.88 ** steps)))
        self.update()

    def keyPressEvent(self, ev):
        k = ev.key()
        if k == Qt.Key_Left:
            self.yaw += 5
        elif k == Qt.Key_Right:
            self.yaw -= 5
        elif k == Qt.Key_Up:
            self.pitch = min(89.0, self.pitch + 5)
        elif k == Qt.Key_Down:
            self.pitch = max(-89.0, self.pitch - 5)
        elif k in (Qt.Key_Plus, Qt.Key_Equal):
            self.dist *= 0.9
        elif k == Qt.Key_Minus:
            self.dist /= 0.9
        elif k in (Qt.Key_Home, Qt.Key_F):
            self.reset_requested.emit()
            return
        else:
            super().keyPressEvent(ev)
            return
        self.dragged.emit()
        self.update()
