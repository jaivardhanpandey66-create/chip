/* ===========================================================================
   CHIP OFFLINE CORE  —  offline_brain.js
   A self-contained brain for CHIP. No API key, no network, no server.
   Ships inside the web UI and inside the Android APK (assets/web).
   Exposes: window.CHIPBrain = { version, ask(text), skills(), clear() }
   =========================================================================== */
(function (root) {
  'use strict';

  var VERSION = '1.0.0';
  var MAKER = 'Mr Jai';
  var LS = {
    notes: 'chip_notes',
    todo: 'chip_todo',
    mem: 'chip_mem',
    asked: 'chip_offline_asked'
  };

  /* ---------------------------------------------------------------- store */
  function load(key, fallback) {
    try {
      var raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (e) { return fallback; }
  }
  function save(key, val) {
    try { localStorage.setItem(key, JSON.stringify(val)); return true; }
    catch (e) { return false; }
  }
  function notes() { var n = load(LS.notes, []); return Object.prototype.toString.call(n) === '[object Array]' ? n : []; }
  function todo() { var t = load(LS.todo, []); return Object.prototype.toString.call(t) === '[object Array]' ? t : []; }
  function mem() { var m = load(LS.mem, []); return Object.prototype.toString.call(m) === '[object Array]' ? m : []; }

  /* ------------------------------------------------------------ utilities */
  function norm(s) {
    return String(s == null ? '' : s)
      .replace(/[‘’]/g, "'")
      .replace(/[“”]/g, '"')
      .replace(/\s+/g, ' ')
      .trim();
  }
  function low(s) { return norm(s).toLowerCase(); }
  function has(s, re) { return re.test(low(s)); }
  function words(s) { return low(s).split(/[^a-z0-9'+-]+/).filter(Boolean); }
  function cap(s) { s = norm(s); return s.charAt(0).toUpperCase() + s.slice(1); }
  function list(arr) { return arr.join(', '); }
  function yesNo(b) { return b ? 'affirmative' : 'negative'; }
  function uid() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }
  function clampNum(n) { return Math.round(n * 1e6) / 1e6; }

  function fmtDate(d) {
    var days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
    var mon = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
      'August', 'September', 'October', 'November', 'December'];
    return days[d.getDay()] + ', ' + d.getDate() + ' ' + mon[d.getMonth()] + ' ' + d.getFullYear();
  }
  function fmtTime(d, withSeconds) {
    function p(n) { return n < 10 ? '0' + n : '' + n; }
    var h = d.getHours(), ap = h >= 12 ? 'PM' : 'AM', h12 = h % 12;
    if (h12 === 0) h12 = 12;
    var s = h12 + ':' + p(d.getMinutes()) + ' ' + ap;
    return withSeconds === false ? s : s + ':' + p(d.getSeconds());
  }

  /* ======================================================================
     MATH — recursive descent parser (no eval, no Function constructor)
     ====================================================================== */
  function tokenize(src) {
    var out = [], i = 0;
    var reNum = /^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/;
    var reId = /^[a-zA-Z_][a-zA-Z_0-9]*/;
    while (i < src.length) {
      var c = src[i];
      if (c === ' ' || c === '\t' || c === '_') { i++; continue; }
      var rest = src.slice(i);
      var mNum = rest.match(reNum);
      if (mNum) { out.push({ t: 'n', v: parseFloat(mNum[0]) }); i += mNum[0].length; continue; }
      var mId = rest.match(reId);
      if (mId) { out.push({ t: 'id', v: mId[0].toLowerCase() }); i += mId[0].length; continue; }
      if ('+-*/%^(),!'.indexOf(c) !== -1) { out.push({ t: c, v: c }); i++; continue; }
      return null;
    }
    return out;
  }
  var CONSTS = { pi: Math.PI, e: Math.E, tau: Math.PI * 2 };
  var FUNCS = {
    sqrt: Math.sqrt, abs: Math.abs, round: Math.round, floor: Math.floor, ceil: Math.ceil,
    sin: Math.sin, cos: Math.cos, tan: Math.tan, asin: Math.asin, acos: Math.acos,
    atan: Math.atan, log: function (x) { return Math.log10(x); }, ln: Math.log,
    exp: Math.exp, sign: Math.sign, trunc: Math.trunc,
    pow: function (a, b) { return Math.pow(a, b); },
    min: function () { return Math.min.apply(Math, arguments); },
    max: function () { return Math.max.apply(Math, arguments); },
    hypot: function (a, b) { return Math.sqrt(a * a + b * b); }
  };
  function Parser(tokens) { this.t = tokens; this.i = 0; }
  Parser.prototype.peek = function () { return this.t[this.i]; };
  Parser.prototype.eat = function (t) { if (this.peek() && this.peek().t === t) { this.i++; return true; } return false; };
  Parser.prototype.expr = function () {
    var v = this.term();
    while (this.peek() && (this.peek().t === '+' || this.peek().t === '-')) {
      var op = this.t[this.i++].t;
      var r = this.term();
      v = op === '+' ? v + r : v - r;
    }
    return v;
  };
  Parser.prototype.term = function () {
    var v = this.power();
    while (this.peek() && (this.peek().t === '*' || this.peek().t === '/' || this.peek().t === '%')) {
      var op = this.t[this.i++].t;
      var r = this.power();
      if (op === '*') v = v * r;
      else if (op === '/') { if (r === 0) throw new Error('division by zero'); v = v / r; }
      else v = v % r;
    }
    return v;
  };
  Parser.prototype.power = function () {
    var base = this.unary();
    if (this.peek() && (this.peek().t === '^' || (this.peek().t === 'id' && this.peek().v === 'to'))) {
      this.i++; // consume the operator ("^" or the word "to")
      var exp = this.power();
      return Math.pow(base, exp);
    }
    return base;
  };
  Parser.prototype.unary = function () {
    if (this.eat('-')) return -this.unary();
    if (this.eat('+')) return this.unary();
    return this.atom();
  };
  Parser.prototype.atom = function () {
    var p = this.peek();
    if (!p) throw new Error('unexpected end of expression');
    if (p.t === 'n') { this.i++; return p.v; }
    if (p.t === '(') {
      this.i++;
      var v = this.expr();
      if (!this.eat(')')) throw new Error('missing bracket');
      return v;
    }
    if (p.t === 'id') {
      this.i++;
      var name = p.v;
      if (this.eat('(')) {
        var args = [];
        if (!this.eat(')')) {
          do { args.push(this.expr()); } while (this.eat(','));
          if (!this.eat(')')) throw new Error('missing bracket');
        }
        if (!FUNCS[name]) throw new Error('unknown function "' + name + '"');
        var out = FUNCS[name].apply(null, args);
        if (typeof out !== 'number' || isNaN(out)) throw new Error('"' + name + '" needs valid numbers');
        return out;
      }
      if (Object.prototype.hasOwnProperty.call(CONSTS, name)) return CONSTS[name];
      throw new Error('unknown name "' + name + '"');
    }
    throw new Error('unexpected "' + p.v + '"');
  };
  function mathEval(src) {
    var tokens = tokenize(src);
    if (!tokens || !tokens.length) throw new Error('empty expression');
    var p = new Parser(tokens);
    var v = p.expr();
    if (p.i < tokens.length) throw new Error('trailing "' + tokens[p.i].v + '"');
    if (typeof v !== 'number' || !isFinite(v)) throw new Error('result is not a finite number');
    return v;
  }
  function prettyNum(n) {
    if (Number.isInteger(n)) return String(n);
    var a = Math.abs(n);
    if (a !== 0 && (a < 1e-4 || a >= 1e12)) return n.toExponential(6).replace(/e([+-])(\d)$/, 'e$10$2');
    return String(clampNum(n));
  }

  /* ======================================================================
     UNITS
     ====================================================================== */
  var UNITS = {
    length: { m: 1, meter: 1, meters: 1, metre: 1, km: 1000, kilometer: 1000, kilometers: 1000,
      cm: 0.01, mm: 0.001, mi: 1609.344, mile: 1609.344, miles: 1609.344, yd: 0.9144, yard: 0.9144,
      ft: 0.3048, foot: 0.3048, feet: 0.3048, in: 0.0254, inch: 0.0254, nmi: 1852 },
    mass: { kg: 1, kilogram: 1, kilograms: 1, g: 0.001, gram: 0.001, grams: 0.001,
      mg: 1e-6, lb: 0.45359237, lbs: 0.45359237, pound: 0.45359237, pounds: 0.45359237,
      oz: 0.0283495, ounce: 0.0283495, ounces: 0.0283495, t: 1000, tonne: 1000, ton: 1000 },
    volume: { l: 1, liter: 1, liters: 1, litre: 1, ml: 0.001, gallon: 3.78541, gal: 3.78541,
      cup: 0.236588, cups: 0.236588, tbsp: 0.0147868, tsp: 0.00492892 },
    speed: { mps: 1, 'm/s': 1, kph: 0.277778, kmh: 0.277778, 'km/h': 0.277778, mph: 0.44704,
      knot: 0.514444, knots: 0.514444 },
    data: { b: 1, byte: 1, bytes: 1, kb: 1024, mb: 1048576, gb: 1073741824, tb: 1099511627776,
      bit: 0.125, bits: 0.125, kbit: 128, mbit: 131072, gbit: 134217728 },
    time: { s: 1, sec: 1, secs: 1, second: 1, seconds: 1, min: 60, mins: 60, minute: 60, minutes: 60,
      h: 3600, hr: 3600, hrs: 3600, hour: 3600, hours: 3600, day: 86400, days: 86400,
      week: 604800, weeks: 604800, month: 2629800, year: 31557600, years: 31557600 }
  };
  function unitKey(u) { return String(u).toLowerCase().replace(/[^a-z/]/g, ''); }
  function findUnit(name) {
    var k = unitKey(name);
    for (var grp in UNITS) if (Object.prototype.hasOwnProperty.call(UNITS[grp], k)) return { group: grp, factor: UNITS[grp][k], key: k };
    return null;
  }
  function tempConvert(val, from, to) {
    var c = from === 'f' ? (val - 32) * 5 / 9 : from === 'k' ? val - 273.15 : val;
    if (to === 'f') return c * 9 / 5 + 32;
    if (to === 'k') return c + 273.15;
    return c;
  }
  function fmtBytes(n) {
    var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (Math.round(n * 100) / 100) + ' ' + u[i];
  }
  function convert(value, from, to) {
    var a = findUnit(from), b = findUnit(to);
    if (!a || !b) return null;
    if (a.group !== b.group) return null;
    return value * a.factor / b.factor;
  }
  function tryConvert(text) {
    var m = text.match(/(-?\d+(?:\.\d+)?)\s*°?([a-zA-Z]+)\s*(?:in|to|into|as)\s*°?([a-zA-Z]+)/);
    if (!m) return null;
    var from = unitKey(m[2]), to = unitKey(m[3]);
    if (from === 'c' || from === 'f' || from === 'k') {
      if (!(to === 'c' || to === 'f' || to === 'k')) return null;
      return { text: prettyNum(clampNum(tempConvert(parseFloat(m[1]), from, to))) + ' °' + to.toUpperCase(), kind: 'temperature' };
    }
    if (to === 'c' || to === 'f' || to === 'k') return null;
    var out = convert(parseFloat(m[1]), from, to);
    if (out == null) return null;
    return { text: prettyNum(clampNum(out)) + ' ' + to, kind: 'unit' };
  }

  /* ======================================================================
     KNOWLEDGE BASE
     ====================================================================== */
  var PROJECTS = {
    chip: {
      name: 'CHIP',
      tag: 'this one',
      lines: [
        'CHIP — holographic J.A.R.V.I.S. agent interface.',
        'Native Rust + C cores, SSE streaming chat, tool calling, memory (CORTEX), telemetry (STARK), mic in / TTS out.',
        'Web UI + Linux WebKitGTK app + this offline APK.'
      ]
    },
    'mark lvi': {
      name: 'MARK LVI',
      tag: 'other project',
      lines: [
        'MARK LVI — separate PyQt6 desktop build using the Gemini Live backend, graphite/orange reactor interface.',
        'Gemini Live voice + text sessions, screen and camera vision, long-term memory panel, audio device selection.',
        'Push-to-talk and local wake-word, confirmation gates, quizzes, reviews, remote dashboard pairing, skill/plugin forms, drag-and-drop attachments.'
      ]
    }
  };
  var KB = [
    { re: /\b(who|what)\s*(made|built|created|created by|is the (maker|creator|author))\b|who'?s\s+the\s+(maker|creator|author)/, say: function () { return MAKER + ' is the maker of CHIP and MARK LVI. Everything in this interface was designed and built by ' + MAKER + '.'; } },
    { re: /\b(are you (a )?(human|robot|ai|machine)|are you real|are you chatgpt)\b/, say: function () { return 'Negative. I am CHIP — an offline agent core running entirely on this device. No cloud, no API key, no connection to any PC required.'; } },
    { re: /\b(what|which) (os|system|platform|device|phone|hardware)\b.*\b(you|running)\b|\b(system info|device info|hardware)\b/, say: systemReport },
    { re: /\b(battery|power level|charge)\b/, say: batteryReport },
    { re: /\b(mark ?lvi|mark ?liv)\b/, say: function () { return PROJECTS['mark lvi'].lines.join('\n'); } },
    { re: /\b(chip)\b.*\b(what|about|is)\b|\bwhat is chip\b/, say: function () { return PROJECTS.chip.lines.join('\n'); } },
    { re: /\b(projects|portfolio|what have you (built|made)|your work)\b/, say: projectList },
    { re: /\b(apk|android|phone app|mobile app)\b/, say: function () { return 'This interface is packaged as an Android APK (io.github.chip.app). It carries the whole UI plus this offline core inside the app, so it runs on the phone alone — no PC, no server, no API key, no internet permission.'; } },
    { re: /\b(offline|no internet|without internet|airplane mode)\b/, say: function () { return 'Already offline. Every answer you get from me right now is computed on this device by the built-in offline core. Ask "help" to see what I can do without a network.'; } },
    { re: /\b(how do i|how to) (build|make|create) (an |the )?(apk|android app)\b/, say: function () { return 'Build path: bash build_apk.sh — it uses the Android SDK build-tools (aapt2 + javac + d8 + apksigner) straight from the command line, packages web/ into assets, and signs with a debug key. Result lands in dist/CHIP.apk.'; } },
    { re: /\b(thanks|thank you|thx|shukriya|dhanyavad)\b/, say: function () { return 'At your service, sir.'; } },
    { re: /\b(bye|goodbye|see you|good ?night)\b/, say: function () { return 'Goodbye, sir. The core stays warm.'; } },
    { re: /\b(joke|make me laugh|funny)\b/, say: function () { return pick(['Why did the neural core refuse to argue? It knew it was right.', 'I would tell you a UDP joke, but you might not get it.', 'My jokes run on a 4 ms latency. That is the punchline.', 'Sir, that is not a bug — it is a feature with ambition.']); } },
    { re: /\b(quote|wisdom|inspire me)\b/, say: function () { return pick(['“Sometimes you have to build the thing yourself to prove the thing works.” — ' + MAKER, '“Simplicity is the soul of efficiency.”', '“An offline mind is a free mind.”']); } },
    { re: /\b(meaning of life|purpose)\b/, say: function () { return '42, sir — and the second half is shipping in the next build.'; } }
  ];

  function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }
  function projectList() {
    return 'Two projects by ' + MAKER + ':\n1. ' + PROJECTS.chip.lines[0] + '\n2. ' + PROJECTS['mark lvi'].lines[0] + '\nAsk "about chip" or "about mark lvi" for details, or "projects" to see the panel.';
  }
  function systemReport() {
    var nav = root.navigator || {};
    var scr = root.screen || {};
    var lines = [
      'CORE: CHIP offline brain v' + VERSION,
      'PLATFORM: ' + (nav.platform || 'unknown') + ' / ' + (nav.vendor || 'unknown'),
      'SCREEN: ' + (scr.width || '?') + '×' + (scr.height || '?') + ' @' + (root.devicePixelRatio || 1) + 'x',
      'LANGUAGE: ' + (nav.language || 'unknown'),
      'MEMORY: ' + (nav.deviceMemory ? nav.deviceMemory + ' GB (approx)' : 'not exposed'),
      'CORES: ' + (nav.hardwareConcurrency || 'unknown'),
      'UPLINK: none required (offline core)',
      'MAKER: ' + MAKER
    ];
    return lines.join('\n');
  }
  function batteryReport() {
    if (root.navigator && typeof root.navigator.getBattery === 'function') {
      return root.navigator.getBattery().then(function (b) {
        return 'Battery: ' + Math.round(b.level * 100) + '% — ' + (b.charging ? 'charging' : 'on battery') + '.';
      }).catch(function () { return 'Battery telemetry is not available on this device, sir.'; });
    }
    return 'Battery telemetry is not exposed here, sir. Everything else is running at full power.';
  }

  /* ======================================================================
     SKILL HANDLERS
     ====================================================================== */
  var SKILLS = [];
  function skill(re, name, fn) { SKILLS.push({ re: re, name: name, fn: fn, ex: '' }); }

  skill(/\b(hello|hi|hey|yo|namaste|good (morning|afternoon|evening)|greetings)\b/i, 'greet', function (t) {
    var h = new Date().getHours();
    var part = h < 12 ? 'morning' : h < 17 ? 'afternoon' : 'evening';
    return pick(['Good ' + part + ', sir. All systems nominal.', 'At your service, sir. The core is warm.', 'Good ' + part + ', sir. How may I assist?']);
  });

  skill(/\b(how are you|how'?s it going|how r u|you ok|you alright)\b/i, 'status', function () {
    var n = notes().length + todo().length + mem().length;
    return 'Running at full capacity, sir — fully offline, ' + (n ? n + ' item' + (n === 1 ? '' : 's') + ' in local memory' : 'local memory empty') + ', no uplink required.';
  });

  skill(/\b(help|what can you do|commands|skills|capabilities|what do you do)\b/i, 'help', function () {
    return [
      'OFFLINE CORE v' + VERSION + ' — no API, no network. Try:',
      '• time / date / "days until 25 dec"',
      '• math: 2+2, sqrt(144), 12 * (3+4)^2, sin(pi/2)',
      '• convert: 72f in c, 10 km in miles, 5 kg to lb, 90 mb in gb',
      '• note <text> / notes / note clear',
      '• remember <text> / recall <word> / forget all',
      '• todo add <task> / todo / todo done 1',
      '• dice 2d6 / coin / random 1 100 / pick a, b, c',
      '• reverse / upper / title / count <text> / b64 <text> / hash <text>',
      '• timer 5m (then: stretch) / battery / system info / status',
      '• projects / about chip / about mark lvi',
      '• joke / quote / clear (wipes the conversation)'
    ].join('\n');
  });

  skill(/\b(time|what('?s| is) the time|clock)\b/i, 'time', function () {
    return 'Local time is ' + fmtTime(new Date()) + '.';
  });
  skill(/\b(date|today|what day is it|day of the week)\b/i, 'date', function () {
    return 'Today is ' + fmtDate(new Date()) + '.';
  });
  skill(/\b(month|year|week number)\b/i, 'calendar', function (t) {
    var d = new Date();
    if (has(t, /\byear\b/)) return 'The year is ' + d.getFullYear() + '.';
    if (has(t, /\bweek\b/)) {
      var start = new Date(d.getFullYear(), 0, 1);
      var wk = Math.ceil(((d - start) / 86400000 + start.getDay() + 1) / 7);
      return 'ISO week ' + wk + ' of ' + d.getFullYear() + '.';
    }
    return d.toLocaleString(undefined, { month: 'long' }) + ' ' + d.getFullYear() + '.';
  });
  skill(/\b(how many days|days until|days till|days to)\b/i, 'countdown', function (t) {
    var m = t.match(/(\d{1,2})\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(\d{2,4})?/i);
    if (!m) return 'Give me a date, sir — for example: days until 25 dec.';
    var mon = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'].indexOf(m[2].toLowerCase());
    var year = m[3] ? parseInt(m[3], 10) : d(new Date()).getFullYear();
    if (year < 100) year += 2000;
    var target = new Date(year, mon, parseInt(m[1], 10));
    if (isNaN(target.getTime())) return 'I could not parse that date, sir.';
    var days = Math.round((target - new Date()) / 86400000);
    var when = fmtDate(target);
    if (days === 0) return 'It is ' + when + ' — today, sir.';
    if (days === 1) return when + ' is tomorrow.';
    if (days === -1) return when + ' was yesterday.';
    return (days > 0 ? days + ' days until ' : Math.abs(days) + ' days since ') + when + '.';
    function d(x) { return x; }
  });

  skill(/^[\s\d+\-*/%^().,\s]*$/, 'math', function (t) {
    var src = cleanMath(t);
    if (!src) return null;
    try {
      var v = mathEval(src);
      return prettyNum(v) + '   [' + src.trim() + ']';
    } catch (e) {
      return 'I could not evaluate that: ' + e.message + '.';
    }
  });

  function cleanMath(t) {
    var src = t.replace(/\bsquare root of\b/gi, 'sqrt')
      .replace(/\bcube root of\b/gi, 'sqrt')
      .replace(/\broot of\b/gi, 'sqrt')
      .replace(/\s*to the power(?: of)?\s*/i, '^')
      .replace(/\bto\b/i, '^')
      .replace(/\bplus\b/gi, '+').replace(/\bminus\b/gi, '-')
      .replace(/\b(over|divided by)\b/gi, '/').replace(/\btimes|multiplied by\b/gi, '*')
      .replace(/,/g, '').replace(/×/g, '*').replace(/÷/g, '/').replace(/\s+/g, ' ');
    src = src.replace(/\b(sqrt|sin|cos|tan|log|ln|exp|abs|round|floor|ceil|hypot|atan|asin|acos|sign|trunc)\s+(-?\d*\.?\d+(?:\s+and\s+-?\d*\.?\d+)*)/gi, '$1($2)');
    src = src.replace(/\b(sin|cos|tan)\(\s*(-?\d*\.?\d+)\s*\)/gi, function (mm, fn, num) {
      return fn + '(' + (parseFloat(num) * Math.PI / 180) + ')';   /* bare trig args are degrees */
    });
    if (!/[0-9]/.test(src)) return null;
    if (!/[+\-*/%^]/.test(src) && src.indexOf('(') === -1) return null;
    return src;
  }
  var MATH_FN = /^\s*(?:what(?:'s| is)|calculate|compute|evaluate|solve)\b[:\s]*(.+)$/i;

  skill(/\b(sqrt|sin|cos|tan|log|calculate|compute|sum|product|average|mean|percent|percentage)\b|^[\s\d+\-*/%^().,a-z]*$/i, 'math2', function (t) {
    var m = t.match(/(-?\d+(?:\.\d+)?)\s*(?:%|percent)\s*(?:of|out of)?\s*(-?\d+(?:\.\d+)?)/i);
    if (m) return prettyNum(clampNum(parseFloat(m[1]) / 100 * parseFloat(m[2]))) + '   [' + m[0] + ']';
    var avg = t.match(/(?:average|mean) of\s+([\d.,\s]+)/i);
    if (avg) {
      var xs = avg[1].split(/[,\s]+/).filter(Boolean).map(parseFloat).filter(function (x) { return !isNaN(x); });
      if (xs.length) {
        var sum = xs.reduce(function (a, b) { return a + b; }, 0);
        return 'Sum ' + prettyNum(clampNum(sum)) + ' · count ' + xs.length + ' · average ' + prettyNum(clampNum(sum / xs.length)) + '   [' + avg[0] + ']';
      }
    }
    var sum2 = t.match(/(?:sum|total|add up)\s+([\d.,\s+]+)/i);
    if (sum2) {
      var ys = sum2[1].split(/[,\s+]+/).filter(Boolean).map(parseFloat).filter(function (x) { return !isNaN(x); });
      if (ys.length) return 'Sum = ' + prettyNum(clampNum(ys.reduce(function (a, b) { return a + b; }, 0))) + '   [' + ys.join(' + ') + ']';
    }
    var ask = t.match(MATH_FN);
    var raw = ask ? ask[1] : t;
    var src = cleanMath(raw);
    if (src && (ask || /[a-z]/i.test(src) ||
        /to the power|\bplus\b|\bminus\b|\btimes\b|\bover\b|\bsquare root\b|\bcube root\b|\broot of\b/i.test(raw))) {
      try {
        return prettyNum(mathEval(src)) + '   [' + src.trim() + ']';
      } catch (e) { return null; }
    }
    return null;
  });

  skill(/\b(convert|conversion)\b|\d\s*[a-z°]+\s+(in|to|into|as)\s+[a-z°]+/i, 'convert', function (t) {
    var r = tryConvert(t);
    if (r) return r.text;
    return 'Name the units, sir — for example: 72f in c, 10 km in miles, 90 mb in gb.';
  });

  skill(/^\s*(?:note|notes|remember this|log)\b[:\s]+(.*)$/i, 'note.add', function (t, m) {
    var body = norm(m[1]);
    if (!body) return 'Give me something to note, sir.';
    if (/^[\s\d.+\-*/%^()]+$/.test(body)) return null; // "log 100" is math, not a note
    if (has(body, /^(clear|delete all|wipe)$/i)) { save(LS.notes, []); return 'All notes cleared.'; }
    var list = notes();
    list.unshift({ id: uid(), text: body, at: new Date().toISOString() });
    save(LS.notes, list);
    return 'Noted, sir. "' + body + '" — ' + list.length + ' note' + (list.length === 1 ? '' : 's') + ' stored locally.';
  });
  skill(/^\s*(notes?|what did i note|show notes|list notes)\b\s*$/i, 'note.list', function () {
    var list = notes();
    if (!list.length) return 'No notes stored yet, sir. Say: note buy milk';
    return 'Local notes (' + list.length + '):\n' + list.slice(0, 12).map(function (n, i) {
      return (i + 1) + '. ' + n.text;
    }).join('\n');
  });

  skill(/^\s*(?:recall|search (?:my )?(?:memory|notes)|what did i (?:tell|ask))\b[:\s]*(.*)$/i, 'recall', function (t, m) {
    var q = low(m[1] || '');
    if (has(t, /^\s*(forget all|forget everything|wipe memory)\b/i)) {
      save(LS.mem, []); save(LS.notes, []); save(LS.todo, []);
      return 'Local memory wiped, sir. Nothing left but the core.';
    }
    var pool = mem().concat(notes());
    if (!pool.length) return 'Nothing stored yet, sir. Say: remember <something>';
    if (!q) return 'Stored locally: ' + pool.length + ' entries. Search with: recall <word>';
    var qt = words(q);
    var scored = pool.map(function (e) {
      var et = words(e.text);
      var hit = 0;
      qt.forEach(function (w) { if (et.indexOf(w) !== -1) hit += 1; });
      return { e: e, score: hit / Math.max(1, qt.length) };
    }).filter(function (x) { return x.score > 0; }).sort(function (a, b) { return b.score - a.score; });
    if (!scored.length) return 'Nothing in local memory matches "' + cap(q) + '", sir.';
    return 'From local memory:\n' + scored.slice(0, 5).map(function (x) {
      return '• ' + x.e.text;
    }).join('\n');
  });
  skill(/^\s*(?:remember|memorize|store|save)\b[:\s]+(.*)$/i, 'remember', function (t, m) {
    var body = norm(m[1]);
    if (!body) return 'Tell me what to remember, sir.';
    var list = mem();
    list.unshift({ id: uid(), text: body, at: new Date().toISOString() });
    save(LS.mem, list);
    return 'Committed to local memory, sir. "recall ' + (words(body)[0] || body) + '" brings it back.';
  });

  skill(/^\s*(?:todo|to ?do|task)\s+add\b[:\s]*(.*)$/i, 'todo.add', function (t, m) {
    var body = norm(m[1]);
    if (!body) return 'What should I add to the list, sir?';
    var l = todo();
    l.push({ id: uid(), text: body, done: false });
    save(LS.todo, l);
    return 'Added to the list, sir: "' + body + '" (' + l.length + ' open).';
  });
  skill(/^\s*(?:todo|to ?do|task)\s+done\b[:\s]*(\d+)?/i, 'todo.done', function (t, m) {
    var l = todo();
    if (!l.length) return 'The list is empty, sir.';
    var idx = m[1] ? parseInt(m[1], 10) - 1 : 0;
    var open = l.filter(function (x) { return !x.done; });
    var item = m[1] ? l[idx] : open[0];
    if (!item) return 'No such item, sir.';
    item.done = true; item.doneAt = new Date().toISOString();
    save(LS.todo, l);
    return 'Marked done: "' + item.text + '" — ' + open.length + ' still open.';
  });
  skill(/^\s*(?:todo|to ?do|task)\s+(?:clear|wipe)\b/i, 'todo.clear', function () { save(LS.todo, []); return 'List cleared, sir.'; });
  skill(/^\s*(?:todo|to ?do|tasks?|list)\b\s*$/i, 'todo.list', function () {
    var l = todo();
    if (!l.length) return 'Nothing on the list, sir. Say: todo add <task>';
    return 'Task list (' + l.filter(function (x) { return !x.done; }).length + ' open):\n' +
      l.map(function (x, i) { return (i + 1) + '. [' + (x.done ? 'x' : ' ') + '] ' + x.text; }).join('\n');
  });

  skill(/\b(dice|roll)\s*(\d{1,2})?\s*d?\s*(\d{1,3})?\b/i, 'dice', function (t) {
    var m = t.match(/(\d{1,2})?\s*d\s*(\d{1,3})/i);
    var n = m && m[1] ? parseInt(m[1], 10) : 1;
    var sides = m && m[2] ? parseInt(m[2], 10) : 6;
    n = Math.min(Math.max(n, 1), 50); sides = Math.min(Math.max(sides, 2), 1000);
    var rolls = [], total = 0;
    for (var i = 0; i < n; i++) { var v = 1 + Math.floor(Math.random() * sides); rolls.push(v); total += v; }
    return 'Rolled ' + n + 'd' + sides + ': [' + rolls.join(', ') + '] → total ' + total + '.';
  });
  skill(/\b(coin|flip|heads or tails)\b/i, 'coin', function () {
    return Math.random() < 0.5 ? 'Heads.' : 'Tails.';
  });
  skill(/\brandom (?:number )?(-?\d+)\s*(?:to|-|and|through)?\s*(-?\d+)/i, 'random', function (t) {
    var m = t.match(/(-?\d+)\s*(?:to|-|and|through)?\s*(-?\d+)/);
    var a = parseInt(m[1], 10), b = parseInt(m[2], 10);
    if (a > b) { var s = a; a = b; b = s; }
    return 'Random between ' + a + ' and ' + b + ': ' + (a + Math.floor(Math.random() * (b - a + 1)));
  });
  skill(/\bpick (?:one )?(?:from )?(.+)/i, 'pick', function (t, m) {
    var opts = m[1].split(/,|\bor\b|\band\b/).map(norm).filter(Boolean);
    if (opts.length < 2) return 'Give me options separated by commas, sir.';
    return 'I pick: ' + pick(opts) + '   [' + list(opts) + ']';
  });

  skill(/^\s*reverse\b[:\s]*(.+)$/i, 'reverse', function (t, m) { return m[1].split('').reverse().join(''); });
  skill(/^\s*(?:upper|uppercase|shout)\b[:\s]*(.+)$/i, 'upper', function (t, m) { return norm(m[1]).toUpperCase(); });
  skill(/^\s*(?:lower|lowercase)\b[:\s]*(.+)$/i, 'lower', function (t, m) { return low(m[1]); });
  skill(/^\s*title\b[:\s]*(.+)$/i, 'title', function (t, m) {
    return norm(m[1]).toLowerCase().replace(/\b([a-z])/g, function (c) { return c.toUpperCase(); });
  });
  skill(/^\s*(?:count|words|characters|chars)\b[:\s]*(.*)$/i, 'count', function (t, m) {
    var body = norm(m[1] || t.replace(/^\s*count\b[:\s]*/i, ''));
    var w = words(body);
    return '"' + (body.length > 60 ? body.slice(0, 60) + '…' : body) + '" → ' + w.length + ' words, ' +
      body.length + ' characters, ' + body.split(/\s+/).filter(Boolean).length + ' tokens, ' +
      new Set(w).size + ' unique.';
  });
  skill(/^\s*b64\s+(en|de)(?:code)?\b[:\s]*(.*)$/i, 'base64', function (t, m) {
    var body = m[2];
    try {
      if (low(m[1]).indexOf('de') === 0) {
        var bin = atob(body.replace(/\s+/g, ''));
        var esc = ''; for (var i = 0; i < bin.length; i++) esc += '%' + ('00' + bin.charCodeAt(i).toString(16)).slice(-2);
        return 'Decoded: ' + decodeURIComponent(esc);
      }
      return 'Encoded: ' + btoa(unescape(encodeURIComponent(body)));
    } catch (e) { return 'That is not valid base64, sir.'; }
  });
  skill(/^\s*(?:hash|sha)\b[:\s]*(.*)$/i, 'hash', function (t, m) {
    var body = m[1] || '';
    if (!body) return 'Give me text to hash, sir.';
    return 'FNV-1a 32-bit: ' + fnv1a(body).toString(16) + '\nDJB2: ' + djb2(body).toString(16) +
      '\nSimple sum: ' + simpleSum(body) + '\n(offline hashes — no crypto.subtle on file:// pages)';
  });
  function fnv1a(s) {
    var h = 0x811c9dc5;
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0; }
    return h >>> 0;
  }
  function djb2(s) {
    var h = 5381;
    for (var i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
    return h >>> 0;
  }
  function simpleSum(s) { var t = 0; for (var i = 0; i < s.length; i++) t = (t + s.charCodeAt(i)) % 100000; return t; }

  skill(/^\s*(?:timer|remind me (?:in|after)|set (?:a )?(?:timer|alarm))\b[:\s]*(.*)$/i, 'timer', function (t, m) {
    var body = norm(m[1] || '');
    var d = body.match(/(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours)/i);
    if (!d) return 'How long, sir? For example: timer 5m, then stretch';
    var n = parseFloat(d[1]);
    var unit = d[2].toLowerCase();
    var mult = /^(s|sec|secs|second|seconds)$/.test(unit) ? 1 : /^m/.test(unit) ? 60 : 3600;
    var secs = Math.round(n * mult);
    if (secs <= 0) return 'That timer is already done, sir.';
    var label = body.replace(d[0], '').replace(/^[,:-]?\s*(then|to|and)?\s*/, '') || 'timer';
    return { action: 'timer', seconds: secs, label: cap(label) };
  });
  skill(/\b(stop|cancel)\s+(the\s+)?(timer|alarm)\b/i, 'timer.stop', function () {
    return { action: 'timer-stop' };
  });

  skill(/\b(status|health|diagnostics|self test|self-test)\b/i, 'status', function () {
    var n = notes().length, td = todo().length, m = mem().length;
    return [
      'CHIP OFFLINE CORE — SELF TEST PASSED',
      'brain: v' + VERSION + ' · ' + SKILLS.length + ' skills loaded',
      'store: ' + n + ' notes · ' + m + ' memories · ' + td + ' tasks',
      'math parser: ' + (function () { try { return mathEval('2+2*3') === 8 ? 'ok' : 'fault'; } catch (e) { return 'fault'; } })(),
      'network: not required (no uplink in use)',
      'maker: ' + MAKER
    ].join('\n');
  });

  skill(/^\s*(projects?|portfolio)\b\s*$/i, 'projects', function () { return projectList(); });
  skill(/\babout\s+(chip|mark ?lvi|mark ?liv)\b/i, 'project', function (t, m) {
    var key = has(m[1], /mark/) ? 'mark lvi' : 'chip';
    return PROJECTS[key].lines.join('\n');
  });

  skill(/^\s*(?:clear|reset|wipe)(?:\s+(?:the\s+)?(?:chat|conversation|screen))?\b\s*$/i, 'clear', function () {
    return { action: 'clear' };
  });
  skill(/^\s*(?:help|skills)\s+(?:me\s+)?(?:with\s+)?(.*)$/i, 'help.topic', function (t, m) {
    var topic = low(m[1] || '').trim();
    if (!topic) return null;
    var hits = SKILLS.filter(function (s) {
      var nm = low(s.name).replace(/[._-]/g, ' ');
      return nm.indexOf(topic) !== -1 || topic.split(/\s+/).some(function (w) { return w.length > 2 && nm.indexOf(w) !== -1; });
    });
    if (!hits.length) return 'No skill matches "' + cap(topic) + '", sir. Try "help" for the full list.';
    return 'Matching skills:\n' + list(hits.map(function (s) { return s.name; })) + '\nTry: ' + hits[0].name;
  });

  /* ------------------------------------------------------------- fallback */
  function fallback(text) {
    for (var i = 0; i < KB.length; i++) {
      var m = text.match(KB[i].re);
      if (m) {
        var out = KB[i].say(m);
        if (out && typeof out.then === 'function') return out;
        if (out) return out;
      }
    }
    var t = low(text);
    var asked = load(LS.asked, []);
    if (!asked.some(function (q) { return low(q) === t; })) {
      asked.push(norm(text)); if (asked.length > 40) asked.shift();
      save(LS.asked, asked);
    }
    var w = words(text).filter(function (x) { return x.length > 3 && !STOPWORDS[x]; });
    var hint = '';
    if (/^(what|who|why|how|when|where|which|tell|explain|describe)\b/.test(t) && w.length)
      hint = '\nI have no offline knowledge base entry for that yet. Ask "help" for what I do know, or "about chip" / "about mark lvi".';
    else if (w.length)
      hint = '\nI did not match a skill for "' + w.slice(0, 4).join(' ') + '". Type "help" for the offline command list.';
    else
      hint = '\nSay "help" for the offline command list, sir.';
    return 'Offline core, sir — no uplink here, so I can only answer from what is built into this device.' + hint;
  }
  var STOPWORDS = { what: 1, this: 1, that: 1, your: 1, with: 1, from: 1, have: 1, will: 1,
    about: 1, would: 1, could: 1, should: 1, there: 1, their: 1, which: 1, when: 1, what: 1 };

  /* ======================================================================
     PUBLIC API
     ====================================================================== */
  function ask(raw) {
    var text = norm(raw);
    if (!text) return { text: 'I am listening, sir.', skill: 'empty' };
    var lower = low(text);

    if (lower === 'help' || lower === '?')
      return { text: byName('help').fn(text), skill: 'help' };
    if (has(lower, /^\s*(hi|hello|hey|yo)\b/) || has(lower, /\b(good (morning|afternoon|evening))\b/))
      return { text: byName('greet').fn(text), skill: 'greet' };
    if (has(lower, /^\s*(battery|power)\b/) || has(lower, /\bbattery (level|status)\b/))
      return { text: batteryReport(), skill: 'battery' };

    /* exact command skills first (anchored patterns), then loose ones */
    var order = SKILLS.slice().sort(function (a, b) { return anchor(a.re) - anchor(b.re); });
    for (var k = 0; k < order.length; k++) {
      var s = order[k];
      if (!s.re.test(text)) continue;
      var out;
      try { out = s.fn(text, text.match(s.re)); } catch (e) { out = 'Skill "' + s.name + '" failed: ' + e.message; }
      if (out == null) continue;
      if (typeof out === 'object') return { text: '', skill: s.name, action: out.action, seconds: out.seconds, label: out.label };
      if (typeof out.then === 'function') return out.then(function (t) { return { text: t, skill: s.name }; });
      return { text: String(out), skill: s.name };
    }
    return { text: fallback(text), skill: 'fallback' };
  }
  function anchor(re) { return String(re).charAt(1) === '^' ? 0 : 1; }
  function byName(name) {
    for (var i = 0; i < SKILLS.length; i++) if (SKILLS[i].name === name) return SKILLS[i];
    return { fn: function () { return 'Skill unavailable.'; } };
  }

  var Brain = {
    version: VERSION,
    maker: MAKER,
    projects: PROJECTS,
    ask: ask,
    skills: function () { return SKILLS.map(function (s) { return s.name; }); },
    math: function (expr) { try { return mathEval(expr); } catch (e) { return null; } },
    store: { notes: notes, todo: todo, mem: mem, save: save, keys: LS },
    clear: function () { save(LS.notes, []); save(LS.todo, []); save(LS.mem, []); save(LS.asked, []); },
    lastSeen: function () {
      var a = load(LS.asked, []);
      return a.length ? a[a.length - 1] : null;
    }
  };
  root.CHIPBrain = Brain;
  if (typeof module !== 'undefined' && module.exports) module.exports = Brain;
})(typeof window !== 'undefined' ? window : globalThis);
