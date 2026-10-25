/* WebGL 后期：辉光（多级降采样/升采样）、色散、暗角、胶片颗粒、故障切片。
 * 2D 画布负责清晰的图形与文字，这里负责光感。 */
(function () {
  const M = window.MF;

  const VS = `attribute vec2 p; varying vec2 uv; void main(){ uv = p*0.5+0.5; gl_Position = vec4(p,0.,1.); }`;
  const FS_BRIGHT = `precision highp float; varying vec2 uv; uniform sampler2D t; uniform vec2 px; uniform float th;
    void main(){
      vec3 c = texture2D(t, uv+px*vec2(-1.,-1.)).rgb + texture2D(t, uv+px*vec2(1.,-1.)).rgb
             + texture2D(t, uv+px*vec2(-1.,1.)).rgb + texture2D(t, uv+px*vec2(1.,1.)).rgb;
      c *= 0.25;
      float l = max(c.r, max(c.g, c.b));
      float k = smoothstep(th, th+0.35, l);
      gl_FragColor = vec4(c*k, 1.);
    }`;
  const FS_DOWN = `precision highp float; varying vec2 uv; uniform sampler2D t; uniform vec2 px;
    void main(){
      vec3 c = texture2D(t, uv).rgb*4.;
      c += texture2D(t, uv+px*vec2(-1.,-1.)).rgb + texture2D(t, uv+px*vec2(1.,-1.)).rgb;
      c += texture2D(t, uv+px*vec2(-1.,1.)).rgb + texture2D(t, uv+px*vec2(1.,1.)).rgb;
      gl_FragColor = vec4(c/8., 1.);
    }`;
  const FS_UP = `precision highp float; varying vec2 uv; uniform sampler2D t; uniform sampler2D base; uniform vec2 px; uniform float w;
    void main(){
      vec3 c = vec3(0.);
      c += texture2D(t, uv+px*vec2(-2.,0.)).rgb + texture2D(t, uv+px*vec2(2.,0.)).rgb;
      c += texture2D(t, uv+px*vec2(0.,-2.)).rgb + texture2D(t, uv+px*vec2(0.,2.)).rgb;
      c += (texture2D(t, uv+px*vec2(-1.,-1.)).rgb + texture2D(t, uv+px*vec2(1.,-1.)).rgb
          + texture2D(t, uv+px*vec2(-1.,1.)).rgb + texture2D(t, uv+px*vec2(1.,1.)).rgb)*2.;
      c /= 12.;
      gl_FragColor = vec4(texture2D(base, uv).rgb + c*w, 1.);
    }`;
  const FS_FINAL = `precision highp float; varying vec2 uv; uniform sampler2D scene; uniform sampler2D bloom;
    uniform float time, bloomK, aberr, grain, vig, flash, fade, glitch; uniform vec2 res; uniform vec3 tint;
    float h(vec2 p){ return fract(sin(dot(p, vec2(12.9898,78.233))) * 43758.5453); }
    void main(){
      vec2 q = uv;
      if (glitch > 0.0) {
        float band = floor(q.y * 36.0);
        float r = h(vec2(band, floor(time*24.0)));
        if (r < glitch*0.8) q.x += (h(vec2(band, floor(time*24.0)+1.7))-0.5) * 0.12 * glitch;
      }
      vec2 d = q - 0.5;
      float r2 = dot(d, d);
      vec2 off = d * (aberr * (0.25 + r2*2.0) + glitch*0.012);
      vec3 c;
      c.r = texture2D(scene, q + off).r;
      c.g = texture2D(scene, q).g;
      c.b = texture2D(scene, q - off).b;
      vec3 b = texture2D(bloom, q).rgb;
      c += b * bloomK;
      c *= tint;
      c += flash;
      // 柔和的肩部压缩，避免辉光叠加后直接截断
      c = c / (1.0 + max(vec3(0.0), c - 0.8) * 0.9);
      c *= mix(1.0, smoothstep(0.95, 0.15, r2*2.2), vig);
      float g = h(q*res + fract(time*13.37)*91.0) - 0.5;
      c += g * grain;
      c *= fade;
      gl_FragColor = vec4(c, 1.);
    }`;

  M.Post = function (glCanvas, srcCanvas, capture) {
    const gl = glCanvas.getContext('webgl', { preserveDrawingBuffer: !!capture, antialias: false, alpha: false });
    if (!gl) throw new Error('WebGL 不可用');
    this.gl = gl;
    this.src = srcCanvas;
    const W = (glCanvas.width = srcCanvas.width), H = (glCanvas.height = srcCanvas.height);

    function sh(type, s) {
      const o = gl.createShader(type);
      gl.shaderSource(o, s);
      gl.compileShader(o);
      if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(o));
      return o;
    }
    const vs = sh(gl.VERTEX_SHADER, VS);
    function prog(fs) {
      const p = gl.createProgram();
      gl.attachShader(p, vs);
      gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs));
      gl.bindAttribLocation(p, 0, 'p');
      gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
      const u = {};
      const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
      for (let i = 0; i < n; i++) {
        const info = gl.getActiveUniform(p, i);
        u[info.name] = gl.getUniformLocation(p, info.name);
      }
      return { p, u };
    }
    this.P = { bright: prog(FS_BRIGHT), down: prog(FS_DOWN), up: prog(FS_UP), final: prog(FS_FINAL) };

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);

    function tex(w, h) {
      const t = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      if (w) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
      return t;
    }
    function fbo(w, h) {
      const t = tex(w, h);
      const f = gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, f);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
      return { t, f, w, h };
    }
    this.sceneTex = tex();
    this.levels = [];
    let w = W >> 1, h = H >> 1;
    for (let i = 0; i < 6; i++) {
      this.levels.push({ down: fbo(w, h), up: fbo(w, h) });
      w = Math.max(1, w >> 1);
      h = Math.max(1, h >> 1);
    }
    this.W = W;
    this.H = H;
  };

  M.Post.prototype.render = function (u) {
    const gl = this.gl, P = this.P, L = this.levels;
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.sceneTex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, this.src);

    const draw = (target, w, h) => {
      gl.bindFramebuffer(gl.FRAMEBUFFER, target);
      gl.viewport(0, 0, w, h);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    };
    // 提取高亮
    gl.useProgram(P.bright.p);
    gl.uniform1i(P.bright.u.t, 0);
    gl.uniform2f(P.bright.u.px, 1 / this.W, 1 / this.H);
    gl.uniform1f(P.bright.u.th, u.threshold ?? 0.28);
    draw(L[0].down.f, L[0].down.w, L[0].down.h);
    // 降采样
    gl.useProgram(P.down.p);
    gl.uniform1i(P.down.u.t, 0);
    for (let i = 1; i < L.length; i++) {
      gl.bindTexture(gl.TEXTURE_2D, L[i - 1].down.t);
      gl.uniform2f(P.down.u.px, 1 / L[i - 1].down.w, 1 / L[i - 1].down.h);
      draw(L[i].down.f, L[i].down.w, L[i].down.h);
    }
    // 升采样并累加
    gl.useProgram(P.up.p);
    gl.uniform1i(P.up.u.t, 0);
    gl.uniform1i(P.up.u.base, 1);
    let prev = L[L.length - 1].down;
    for (let i = L.length - 2; i >= 0; i--) {
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, L[i].down.t);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, prev.t);
      gl.uniform2f(P.up.u.px, 1 / prev.w, 1 / prev.h);
      gl.uniform1f(P.up.u.w, 0.9);
      draw(L[i].up.f, L[i].up.w, L[i].up.h);
      prev = L[i].up;
    }
    // 合成
    gl.useProgram(P.final.p);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.sceneTex);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, prev.t);
    const U = P.final.u;
    gl.uniform1i(U.scene, 0);
    gl.uniform1i(U.bloom, 1);
    gl.uniform1f(U.time, u.time);
    gl.uniform1f(U.bloomK, u.bloom ?? 1.1);
    gl.uniform1f(U.aberr, u.aberr ?? 0.004);
    gl.uniform1f(U.grain, u.grain ?? 0.035);
    gl.uniform1f(U.vig, u.vig ?? 0.85);
    gl.uniform1f(U.flash, u.flash ?? 0);
    gl.uniform1f(U.fade, u.fade ?? 1);
    gl.uniform1f(U.glitch, u.glitch ?? 0);
    gl.uniform2f(U.res, this.W, this.H);
    const tint = u.tint || [1, 1, 1];
    gl.uniform3f(U.tint, tint[0], tint[1], tint[2]);
    draw(null, this.W, this.H);
    gl.activeTexture(gl.TEXTURE0);
  };
})();
