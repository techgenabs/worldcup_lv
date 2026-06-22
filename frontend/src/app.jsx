// Destructure hooks from global React (loaded via CDN)
const { useEffect, useRef, useState, useCallback } = React;

// ── Safe XLSX loader ──────────────────────────────────────────────────────────
// Loads XLSX from CDN if not already available, then runs callback
function withXLSX(callback) {
  // Already loaded
  if (window.XLSX) { callback(window.XLSX); return; }

  // Try loading from CDN dynamically
  const script = document.createElement("script");
  script.src = "https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js";
  script.onload  = () => { if (window.XLSX) callback(window.XLSX); else alert("XLSX failed to load. Check your internet connection."); };
  script.onerror = () => alert("Could not load Excel library. Check your internet connection.");
  document.head.appendChild(script);
}

// Export a single match's predictions to Excel — filename uses the game
// name (teams) and date, e.g. "Mexico_vs_USA_11-Jun-2026.xlsx"
function exportSingleMatch(match, preds) {
  if (!match) return;
  const matchLabel = (match.home_team || "TBD") + " vs " + (match.away_team || "TBD");
  const rows = (preds || []).map((p, i) => {
    const o = p.scoring_reason || p.status || "pending";
    return {
      "#":             i + 1,
      Match:           matchLabel,
      "Game No":       match.game_no || "",
      Round:           match.round || "",
      "Date (NPT)":    nepaliTime(match.match_date),
      Venue:           match.stadium || "",
      Player:          p.name || p.user_name || ("User #" + p.user_id),
      Email:           p.email || p.user_email || "",
      Country:         p.user_country || p.country || "",
      Predicted:       p.predicted_home_score + "-" + p.predicted_away_score,
      "Final Score":   match.status === "completed" ? match.home_score + "-" + match.away_score : "Pending",
      Outcome:         o.replace(/_/g, " "),
      Points:          o !== "pending" ? (p.points_awarded || 0) : "",
      "Submitted At":  p.created_at ? new Date(p.created_at).toLocaleString() : "",
    };
  });

  const summary = [
    { Field: "Match",         Value: matchLabel },
    { Field: "Game No",       Value: match.game_no || "" },
    { Field: "Round/Group",   Value: match.round || "" },
    { Field: "Home Team",     Value: match.home_team || "" },
    { Field: "Away Team",     Value: match.away_team || "" },
    { Field: "Kickoff (NPT)", Value: nepaliTime(match.match_date) },
    { Field: "Venue",         Value: match.stadium || "" },
    { Field: "Status",        Value: match.status || "" },
    { Field: "Final Score",   Value: match.status === "completed" ? match.home_score + "-" + match.away_score : "Pending" },
    { Field: "Total Participants", Value: preds.length },
    { Field: "Exported On",   Value: new Date().toLocaleString() },
  ];

  // Build filename as "<Home> vs <Away> - <Date>" — sanitized for filesystem
  const dateLabel = match.match_date
    ? new Date(match.match_date).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }).replace(/\s+/g, "-")
    : "TBD";
  const safeName = (s) => (s || "TBD").replace(/[\\/:*?"<>|]/g, "").replace(/\s+/g, "_");
  const filename = `${safeName(match.home_team)}_vs_${safeName(match.away_team)}_${dateLabel}`;

  downloadXLSX(filename, [
    { name: "Predictions", rows },
    { name: "Match Info",  rows: summary },
  ]);
}

// Helper: build and download an xlsx workbook from multiple sheets
// sheets = [ { name: "Sheet1", rows: [...] }, ... ]
function downloadXLSX(filename, sheets) {
  withXLSX(XLSX => {
    const wb = XLSX.utils.book_new();
    sheets.forEach(s => {
      const ws = XLSX.utils.json_to_sheet(s.rows.length ? s.rows : [{ Note: "No data" }]);
      // Auto-width columns
      const colWidths = {};
      s.rows.forEach(row => {
        Object.entries(row).forEach(([k, v]) => {
          colWidths[k] = Math.max(colWidths[k] || k.length, String(v ?? "").length);
        });
      });
      ws["!cols"] = Object.keys(colWidths).map(k => ({ wch: Math.min(colWidths[k] + 2, 50) }));
      XLSX.utils.book_append_sheet(wb, ws, s.name.slice(0, 31)); // sheet name max 31 chars
    });
    XLSX.writeFile(wb, filename.endsWith(".xlsx") ? filename : filename + ".xlsx");
  });
}

// ─────────────────────────────────────────────────────────────────────────────
//  API LAYER  — unchanged from your original
// ─────────────────────────────────────────────────────────────────────────────
const api = {
  token: localStorage.getItem("wc_token") || "",

  async request(path, options = {}) {
    const response = await fetch(`/api${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
        ...(options.headers || {}),
      },
    });

    // Safely read body — could be JSON or HTML error page
    const contentType = response.headers.get("content-type") || "";
    let body = null;
    if (contentType.includes("application/json")) {
      try { body = await response.json(); } catch { body = null; }
    } else {
      // Non-JSON response (HTML 404/500 page etc.)
      const text = await response.text();
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText} — ${path}`);
      }
      return text;
    }

    if (!response.ok) {
      let msg = `${response.status} ${response.statusText}`;
      if (body) {
        const d = body.detail || body.message || body.error;
        if (typeof d === "string") {
          msg = d;
        } else if (Array.isArray(d)) {
          // FastAPI/Pydantic validation errors: list of {loc, msg, type}
          msg = d.map(item => {
            if (typeof item === "string") return item;
            const field = Array.isArray(item.loc) ? item.loc.join(".") : item.loc || "";
            return `${field ? field + ": " : ""}${item.msg || JSON.stringify(item)}`;
          }).join("; ");
        } else if (d && typeof d === "object") {
          msg = d.msg || d.message || JSON.stringify(d);
        }
      }
      throw new Error(msg);
    }

    return body;
  },
};

// ─────────────────────────────────────────────────────────────────────────────
//  CONSTANTS
// ─────────────────────────────────────────────────────────────────────────────
const FLAG_BY_NAME = {
  // ── 48 confirmed FIFA World Cup 2026 teams ──
  Mexico:"🇲🇽", "South Korea":"🇰🇷", Czechia:"🇨🇿", "South Africa":"🇿🇦",
  Canada:"🇨🇦", "Bosnia and Herzegovina":"🇧🇦", Qatar:"🇶🇦", Switzerland:"🇨🇭",
  Brazil:"🇧🇷", Morocco:"🇲🇦", Haiti:"🇭🇹", Scotland:"🏴",
  USA:"🇺🇸", "United States":"🇺🇸", Paraguay:"🇵🇾", Australia:"🇦🇺", Turkiye:"🇹🇷",
  Germany:"🇩🇪", Curacao:"🇨🇼", "Curaçao":"🇨🇼", "Ivory Coast":"🇨🇮", Ecuador:"🇪🇨",
  Netherlands:"🇳🇱", Japan:"🇯🇵", Sweden:"🇸🇪", Tunisia:"🇹🇳",
  Belgium:"🇧🇪", Egypt:"🇪🇬", Iran:"🇮🇷", "New Zealand":"🇳🇿",
  Spain:"🇪🇸", "Cape Verde":"🇨🇻", "Saudi Arabia":"🇸🇦", Uruguay:"🇺🇾",
  France:"🇫🇷", Senegal:"🇸🇳", Iraq:"🇮🇶", Norway:"🇳🇴",
  Argentina:"🇦🇷", Algeria:"🇩🇿", Austria:"🇦🇹", Jordan:"🇯🇴",
  Portugal:"🇵🇹", "Congo DR":"🇨🇩", Uzbekistan:"🇺🇿", Colombia:"🇨🇴",
  England:"🏴󠁧󠁢󠁥󠁮󠁧󠁿", Croatia:"🇭🇷", Ghana:"🇬🇭", Panama:"🇵🇦",
  // ── Extras occasionally referenced (host-confederation neighbours etc.) ──
  Nepal:"🇳🇵", Nigeria:"🇳🇬", Poland:"🇵🇱", Serbia:"🇷🇸", Denmark:"🇩🇰",
  Cameroon:"🇨🇲", "Costa Rica":"🇨🇷", "European Play-off Winner":"🏆",
};

const SPORTS = ["FIFA World Cup", "UEFA Champions League", "AFC Cup", "Volleyball", "Cricket", "Kabaddi", "Other"];
const ROUNDS  = [
  "Group A","Group B","Group C","Group D","Group E","Group F",
  "Group G","Group H","Group I","Group J","Group K","Group L",
  "Round of 32","Round of 16","Quarter-Final","Semi-Final","3rd Place","Final",
];

// All 211 FIFA member countries (alphabetical) — used for the registration
// country dropdown so no real country is ever missing from the list.
const WC_COUNTRIES = [
  "Afghanistan","Albania","Algeria","American Samoa","Andorra","Angola",
  "Anguilla","Antigua and Barbuda","Argentina","Armenia","Aruba","Australia",
  "Austria","Azerbaijan","Bahamas","Bahrain","Bangladesh","Barbados","Belarus",
  "Belgium","Belize","Benin","Bermuda","Bhutan","Bolivia","Bosnia and Herzegovina",
  "Botswana","Brazil","British Virgin Islands","Brunei","Bulgaria","Burkina Faso",
  "Burundi","Cambodia","Cameroon","Canada","Cape Verde","Cayman Islands",
  "Central African Republic","Chad","Chile","China","Chinese Taipei","Colombia",
  "Comoros","Congo","Congo DR","Cook Islands","Costa Rica","Croatia","Cuba",
  "Curaçao","Cyprus","Czechia","Denmark","Djibouti","Dominica",
  "Dominican Republic","Ecuador","Egypt","El Salvador","England","Equatorial Guinea",
  "Eritrea","Estonia","Eswatini","Ethiopia","Faroe Islands","Fiji","Finland",
  "France","Gabon","Gambia","Georgia","Germany","Ghana","Gibraltar","Greece",
  "Grenada","Guam","Guatemala","Guinea","Guinea-Bissau","Guyana","Haiti",
  "Honduras","Hong Kong","Hungary","India","Indonesia","Iran","Iraq","Ireland",
  "Israel","Italy","Ivory Coast","Jamaica","Japan","Jordan","Kazakhstan","Kenya",
  "Kosovo","Kuwait","Kyrgyzstan","Laos","Latvia","Lebanon","Lesotho","Liberia",
  "Libya","Liechtenstein","Lithuania","Luxembourg","Macau","Madagascar","Malawi",
  "Malaysia","Maldives","Mali","Malta","Mauritania","Mauritius","Mexico",
  "Moldova","Mongolia","Montenegro","Montserrat","Morocco","Mozambique","Myanmar",
  "Namibia","Nepal","Netherlands","New Caledonia","New Zealand","Nicaragua",
  "Niger","Nigeria","North Korea","North Macedonia","Northern Ireland","Norway",
  "Oman","Pakistan","Palestine","Panama","Papua New Guinea","Paraguay","Peru",
  "Philippines","Poland","Portugal","Puerto Rico","Qatar","Romania","Russia",
  "Rwanda","Samoa","San Marino","São Tomé and Príncipe","Saudi Arabia","Scotland",
  "Senegal","Serbia","Seychelles","Sierra Leone","Singapore","Slovakia","Slovenia",
  "Solomon Islands","Somalia","South Africa","South Korea","South Sudan","Spain",
  "Sri Lanka","St Kitts and Nevis","St Lucia","St Vincent and the Grenadines",
  "Sudan","Suriname","Sweden","Switzerland","Syria","Tahiti","Tajikistan",
  "Tanzania","Thailand","Timor-Leste","Togo","Tonga","Trinidad and Tobago",
  "Tunisia","Turkiye","Turkmenistan","Turks and Caicos Islands","Uganda","Ukraine",
  "United Arab Emirates","United States","Uruguay","US Virgin Islands","Uzbekistan",
  "Vanuatu","Venezuela","Vietnam","Wales","Yemen","Zambia","Zimbabwe","Other",
];

// ─────────────────────────────────────────────────────────────────────────────
//  HELPERS
// ─────────────────────────────────────────────────────────────────────────────
function flagFor(name, saved) {
  return FLAG_BY_NAME[name] || saved || "🏆";
}

// All times shown in Nepal timezone — kept exactly as your original
function nepaliTime(value) {
  if (!value) return "Date TBD";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-US", {
    timeZone: "Asia/Kathmandu",
    year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  }) + " NPT";
}

function fmtDateShort(value) {
  if (!value) return "TBD";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value
    : d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

// Date-only label in Nepal Time, used as the grouping key/header for the
// date-wise predictions view (e.g. "Thu, 11 Jun 2026")
function nepaliDateLabel(value) {
  if (!value) return "Date TBD";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "Date TBD";
  return d.toLocaleDateString("en-US", {
    timeZone: "Asia/Kathmandu",
    weekday: "short", day: "2-digit", month: "short", year: "numeric",
  });
}

// Sortable key (YYYY-MM-DD in NPT) so date groups order correctly regardless
// of display format
function nepaliDateKey(value) {
  if (!value) return "9999-99-99";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "9999-99-99";
  // en-CA gives YYYY-MM-DD directly
  return d.toLocaleDateString("en-CA", { timeZone: "Asia/Kathmandu" });
}

// ─────────────────────────────────────────────────────────────────────────────
//  SMALL UI ATOMS
// ─────────────────────────────────────────────────────────────────────────────

// Toast notification
function Toast({ message, type, onClose }) {
  useEffect(() => {
    if (!message) return;
    const t = setTimeout(onClose, 4000);
    return () => clearTimeout(t);
  }, [message]);
  if (!message) return null;
  const styles = {
    success: "border-emerald-400 bg-emerald-50 text-emerald-800",
    error:   "border-red-400 bg-red-50 text-red-800",
    info:    "border-blue-400 bg-blue-50 text-blue-800",
  };
  return (
    <div className={`fixed bottom-6 left-1/2 -translate-x-1/2 z-50 border rounded-xl px-5 py-3 font-bold shadow-xl text-sm max-w-sm text-center ${styles[type] || styles.info}`}
      style={{ animation: "slideUp .25s ease" }}>
      {message}
    </div>
  );
}

// Generic modal wrapper
function Modal({ open, onClose, title, children }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto"
        style={{ animation: "slideUp .25s ease" }}>
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <h3 className="font-black text-lg tracking-wide">{title}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700 text-xl font-black leading-none">✕</button>
        </div>
        <div className="px-6 py-5">{children}</div>
      </div>
    </div>
  );
}

// Stat card used in dashboard header and My Predictions
function StatCard({ label, value, sub, icon, accentClass = "border-emerald-500" }) {
  return (
    <div className={`card p-4 border-t-4 ${accentClass}`}>
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs font-bold uppercase tracking-widest text-slate-400">{label}</div>
          <div className="mt-1 text-3xl font-black text-slate-900">{value}</div>
          {sub && <div className="text-xs text-slate-500 mt-1">{sub}</div>}
        </div>
        {icon && <span className="text-2xl opacity-50">{icon}</span>}
      </div>
    </div>
  );
}

// Chart panel (Chart.js bar)
function ChartPanel({ title, labels, values }) {
  const canvasRef = useRef(null);
  useEffect(() => {
    if (!canvasRef.current) return;
    const chart = new Chart(canvasRef.current, {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: title,
          data: values,
          backgroundColor: labels.map((_, i) =>
            ["#007c5a","#c22238","#f1b434","#17202a","#2563eb","#7c3aed"][i % 6]
          ),
          borderRadius: 6,
        }],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, grid: { color: "#f1f5f9" } }, x: { grid: { display: false } } },
      },
    });
    return () => chart.destroy();
  }, [labels.join(","), values.join(",")]);
  return (
    <div className="card p-4">
      <h3 className="font-black text-sm uppercase tracking-widest text-slate-500 mb-4">{title}</h3>
      {labels.length
        ? <canvas ref={canvasRef} height="150" />
        : <div className="text-center py-8 text-slate-400 text-sm">No data yet</div>}
    </div>
  );
}

// Status badge — inline styles so it works regardless of CSS loading
function StatusPill({ status }) {
  const styles = {
    scheduled: { background:"#dbeafe", color:"#1d4ed8" },
    completed: { background:"#d1fae5", color:"#065f46" },
    live:      { background:"#fee2e2", color:"#b91c1c" },
    locked:    { background:"#fef3c7", color:"#92400e" },
  };
  const s = styles[status] || { background:"#f1f5f9", color:"#64748b" };
  const label = status === "live" ? "🔴 LIVE"
    : status ? status.charAt(0).toUpperCase() + status.slice(1) : "—";
  return (
    <span style={{
      ...s,
      display:"inline-flex", alignItems:"center",
      padding:"3px 10px", borderRadius:"20px",
      fontSize:"11px", fontWeight:700,
      textTransform:"uppercase", letterSpacing:"0.05em",
      whiteSpace:"nowrap",
    }}>
      {label}
    </span>
  );
}

// Outcome badge for predictions
function OutcomePill({ outcome }) {
  const map = {
    exact_score:    ["bg-yellow-100 text-yellow-800 border border-yellow-300", "🥇 Exact Score"],
    correct_winner: ["bg-emerald-100 text-emerald-800 border border-emerald-300", "✅ Correct"],
    wrong:          ["bg-red-100 text-red-700 border border-red-200", "❌ Wrong"],
    pending:        ["bg-slate-100 text-slate-500", "⏳ Pending"],
  };
  const [cls, label] = map[outcome] || map.pending;
  return <span className={`pill text-xs font-bold ${cls}`}>{label}</span>;
}

// ─────────────────────────────────────────────────────────────────────────────
//  LOGIN  — your original component, untouched
// ─────────────────────────────────────────────────────────────────────────────
function Login({ onLogin }) {
  const [mode, setMode] = useState("login");
  const [name, setName] = useState("");
  const [identifier, setIdentifier] = useState("");
  const [mobile, setMobile] = useState("");
  const [country, setCountry] = useState("Global"); // Country selection currently disabled — see commented dropdown below. Defaults to "Global" so registration works without it. To re-enable: change default back to "" and uncomment the <select> block in the form.
  const [password, setPassword] = useState("");
  const [requirements, setRequirements] = useState({ email_required: true, mobile_required: false, otp_required: false });
  const [error, setError] = useState("");

  useEffect(() => {
    api.request("/auth/registration-settings").then(setRequirements).catch(() => {});
  }, []);

  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      const data = mode === "login"
        ? await api.request("/auth/login", { method: "POST", body: JSON.stringify({ identifier, password }) })
        : await api.request("/auth/register", {
            method: "POST",
            body: JSON.stringify({
              name,
              email: identifier.trim() || null,
              mobile: mobile.trim() || null,
              country,
              password,
            }),
          });

      // If registration returns a message (not a token), the account was
      // created but is inactive — show the message and switch to login mode
      // so the user knows not to try logging in yet.
      if (mode === "register" && !data.access_token) {
        setError("✅ Account created! " + (data.message || "Please wait for admin to activate your account before logging in."));
        setMode("login");
        setPassword("");
        return;
      }

      api.token = data.access_token;
      localStorage.setItem("wc_token", data.access_token);
      onLogin(data.user);
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <main className="min-h-screen wc-bg grid place-items-center p-4">
      <section className="glass max-w-md w-full p-6 rounded-lg shadow-2xl">
        <div className="text-sm font-black uppercase tracking-wide text-emerald-800">WorldCup 2026</div>
        <h1 className="mt-2 text-4xl font-black text-slate-950">Servey center</h1>
        <p className="mt-3 text-slate-700">Predict exact scores, lock picks before kickoff, track points, ranks, reports, and results.</p>
        <div className="mt-5 grid grid-cols-2 gap-2 rounded-lg bg-white/60 p-1">
          <button type="button" className={`btn ${mode === "login" ? "btn-primary" : "bg-white"}`} onClick={() => setMode("login")}>Sign in</button>
          <button type="button" className={`btn ${mode === "register" ? "btn-primary" : "bg-white"}`} onClick={() => { setMode("register"); setIdentifier(""); setPassword(""); }}>Create account</button>
        </div>
        <form onSubmit={submit} className="mt-6 space-y-3">
          {mode === "register" && <input className="input" value={name} onChange={e => setName(e.target.value)} placeholder="Full name" required />}
          <input className="input" value={identifier} onChange={e => setIdentifier(e.target.value)}
            placeholder={mode === "login" ? "Email or mobile" : `Email address${requirements.email_required ? "" : " (optional)"}`}
            required={mode === "login" || requirements.email_required} />
          {mode === "register" && <input className="input" value={mobile} onChange={e => setMobile(e.target.value)} placeholder={`Mobile number${requirements.mobile_required ? "" : " (optional)"}`} required={requirements.mobile_required} />}
          {/* Country selection currently DISABLED per request — uncomment this block
              to re-enable it, and change the useState default above back to "" so
              the placeholder "Select your country…" shows again on a fresh form.
          {mode === "register" && (
            <select className="input" value={country} onChange={e => setCountry(e.target.value)}>
              <option value="">Select your country…</option>
              {WC_COUNTRIES.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          )}
          */}
          <input className="input" type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Password" required />
          {error && (
            <div style={{
              fontSize:"13px",fontWeight:700,padding:"10px 12px",borderRadius:"8px",
              background: error.startsWith("✅") ? "#f0fdf4" : "#fef2f2",
              color:      error.startsWith("✅") ? "#065f46" : "#b91c1c",
              border:     "1px solid " + (error.startsWith("✅") ? "#86efac" : "#fca5a5"),
            }}>{error}</div>
          )}
          <button className="btn btn-primary w-full">{mode === "login" ? "Sign in" : "Create account"}</button>
          {mode === "register" && (
            <div style={{background:"#fffbeb",border:"1px solid #fde68a",borderRadius:"10px",padding:"12px 14px",marginTop:"4px"}}>
              <div style={{display:"flex",gap:"8px",alignItems:"flex-start"}}>
                <span style={{fontSize:"16px",flexShrink:0}}>⚠️</span>
                <div style={{fontSize:"12px",color:"#92400e",lineHeight:1.6}}>
                  <strong>New accounts are inactive by default.</strong><br/>
                  Your account will be reviewed and activated by the admin before you can log in.<br/>
                  For any issues, contact: <a href="mailto:singhamarpkr@gmail.com"
                    style={{color:"#b45309",fontWeight:700,textDecoration:"underline"}}>
                    singhamarpkr@gmail.com
                  </a>
                </div>
              </div>
            </div>
          )}
          {mode === "login" && (
            <div style={{textAlign:"center",fontSize:"11px",color:"#94a3b8",marginTop:"4px"}}>
              Having trouble logging in? Contact <a href="mailto:singhamarpkr@gmail.com"
                style={{color:"#64748b",fontWeight:700}}>singhamarpkr@gmail.com</a>
            </div>
          )}
        </form>
      </section>
      <div className="mt-6 text-xs text-slate-500 tracking-wide">~~~~~developed by/abs@techgen~~~~~</div>
    </main>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  ADMIN — ADD MATCH FORM  (your original logic, cleaned up)
// ─────────────────────────────────────────────────────────────────────────────
function AdminMatchForm({ selected, teams, onSaved }) {
  const blank = {
    game_no: "", sport: "FIFA World Cup", round: "Group Stage",
    stadium: "", match_date: "", home_team_id: "", away_team_id: "", result_mode: "manual",
  };
  const [form, setForm] = useState(blank);
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  async function submit(e) {
    e.preventDefault();
    if (form.home_team_id === form.away_team_id) { alert("Home and away teams must be different."); return; }
    await api.request("/matches", {
      method: "POST",
      body: JSON.stringify({ ...form, tournament_id: selected, home_team_id: Number(form.home_team_id), away_team_id: Number(form.away_team_id) }),
    });
    setForm(blank);
    onSaved("✅ Match created successfully.");
  }

  return (
    <div className="card p-5">
      <h2 className="font-black text-sm uppercase tracking-widest text-slate-500 mb-4">➕ Create New Match</h2>
      <form onSubmit={submit} className="grid md:grid-cols-4 gap-3">
        <div><label className="label">Game No</label><input className="input" value={form.game_no} onChange={e => set("game_no", e.target.value)} placeholder="e.g. G-01" /></div>
        <div>
          <label className="label">Sport</label>
          <select className="input" value={form.sport} onChange={e => set("sport", e.target.value)}>
            {SPORTS.map(s => <option key={s}>{s}</option>)}
          </select>
        </div>
        <div>
          <label className="label">Round / Stage</label>
          <select className="input" value={form.round} onChange={e => set("round", e.target.value)}>
            {ROUNDS.map(r => <option key={r}>{r}</option>)}
          </select>
        </div>
        <div><label className="label">Venue</label><input className="input" value={form.stadium} onChange={e => set("stadium", e.target.value)} placeholder="Stadium, City" /></div>
        <div><label className="label">Kickoff Date &amp; Time</label><input className="input" type="datetime-local" value={form.match_date} onChange={e => set("match_date", e.target.value)} required /></div>
        <div>
          <label className="label">Home Team</label>
          <select className="input" value={form.home_team_id} onChange={e => set("home_team_id", e.target.value)} required>
            <option value="">Select Team A</option>
            {teams.map(t => <option key={t.id} value={t.id}>{flagFor(t.name, t.flag)} {t.name}</option>)}
          </select>
        </div>
        <div>
          <label className="label">Away Team</label>
          <select className="input" value={form.away_team_id} onChange={e => set("away_team_id", e.target.value)} required>
            <option value="">Select Team B</option>
            {teams.map(t => <option key={t.id} value={t.id}>{flagFor(t.name, t.flag)} {t.name}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="label">Result Mode</label>
          <select className="input" value={form.result_mode} onChange={e => set("result_mode", e.target.value)}>
            <option value="manual">Manual entry</option>
            <option value="auto">Auto (API)</option>
          </select>
          <button className="btn btn-primary mt-auto">Create Match</button>
        </div>
      </form>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  PREDICTION CONTROLS  (your original logic, small UX polish)
// ─────────────────────────────────────────────────────────────────────────────
function PredictionControls({ match, existing, onSaved }) {
  const [home, setHome] = useState(existing?.predicted_home_score ?? "");
  const [away, setAway] = useState(existing?.predicted_away_score ?? "");
  const [saving, setSaving] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setSaving(true);
    try {
      await api.request("/predictions", {
        method: "POST",
        body: JSON.stringify({ match_id: match.id, predicted_home_score: Number(home), predicted_away_score: Number(away) }),
      });
      onSaved("🎯 Prediction saved. You can edit it until 5 minutes before kickoff.");
    } catch (err) {
      onSaved("⚠️ " + err.message);
    }
    setSaving(false);
  }

  return (
    <form onSubmit={submit} className="mt-3 pt-3 border-t border-dashed border-slate-200">
      <div className="text-xs font-bold uppercase tracking-widest text-slate-400 mb-2">
        {existing ? "✏️ Update your prediction" : "🎯 Enter your prediction"}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-slate-600">{flagFor(match.home_team, match.home_flag)} {match.home_team}</span>
          <input className="input w-16 text-center font-black text-xl" type="number" min="0" max="20" value={home} onChange={e => setHome(e.target.value)} required />
        </div>
        <span className="font-black text-slate-400 text-xl">–</span>
        <div className="flex items-center gap-2">
          <input className="input w-16 text-center font-black text-xl" type="number" min="0" max="20" value={away} onChange={e => setAway(e.target.value)} required />
          <span className="text-sm font-bold text-slate-600">{flagFor(match.away_team, match.away_flag)} {match.away_team}</span>
        </div>
        <button className="btn btn-primary" disabled={saving}>{saving ? "Saving…" : "Save Prediction"}</button>
      </div>
    </form>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  MATCH CARD  — used in Games page and Admin match management
// ─────────────────────────────────────────────────────────────────────────────
// Shorten long team names for display inside cards
function shortName(name) {
  if (!name) return "";
  const MAP = {
    "European Play-off Winner": "Euro P/O",
    "Inter-confederation Play-off 1": "IC P/O 1",
    "Inter-confederation Play-off 2": "IC P/O 2",
  };
  return MAP[name] || (name.length > 14 ? name.slice(0, 13) + "…" : name);
}

// ── Global kickoff helper — used by MatchCard, GamesPage, PredictionListPage ──
// Returns the effective status, accounting for DB lag (still "scheduled" after kickoff)
function effectiveMatchStatus(match) {
  if (!match) return "scheduled";
  if (match.status !== "scheduled") return match.status;
  if (!match.match_date) return "scheduled";
  // If kickoff time has passed → treat as live (DB hasn't been updated yet)
  return new Date(match.match_date) < new Date() ? "live" : "scheduled";
}

function kickoffPassed(match) {
  if (!match || !match.match_date) return false;
  return new Date(match.match_date) < new Date();
}

function MatchCard({ match, user, myPrediction, onAction, onPredSaved }) {
  const isAdmin  = user.role === "admin";

  // Use effective status — handles DB lag where status stays "scheduled" after kickoff
  const effStatus  = effectiveMatchStatus(match);
  const kicked     = kickoffPassed(match);

  // Can predict: only if match is open AND kickoff hasn't passed yet
  const canPredict = !isAdmin && match.status === "scheduled" && !kicked && Boolean(match.predictions_open);

  // Colour strip
  const stripColor = {
    live:      "bg-red-500",
    completed: "bg-emerald-500",
    locked:    "bg-amber-400",
    scheduled: "bg-blue-400",
  }[effStatus] || "bg-slate-300";

  // Show score once live or completed
  const hasScore  = effStatus === "completed" || effStatus === "live";
  const homeScore = match.home_score ?? 0;
  const awayScore = match.away_score ?? 0;

  return (
    <div className={`match-card${effStatus === "live" ? " ring-2 ring-red-400" : ""}`}>
      {/* Top colour strip */}
      <div className={`match-status-strip ${stripColor}`} />

      <div className="p-4">
        {/* ── Meta row ── */}
        <div className="flex items-start justify-between gap-2 mb-3">
          <div style={{minWidth:0,flex:1}}>
            <div style={{fontSize:"10px",fontWeight:800,textTransform:"uppercase",letterSpacing:"0.08em",color:"#94a3b8",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>
              {match.game_no || `#${match.id}`} · {match.sport || "FIFA"} · {match.round || "Group"}
            </div>
            <div style={{fontSize:"11px",color:"#64748b",marginTop:"2px"}}>📍 {match.stadium || "Venue TBD"}</div>
            <div style={{fontSize:"11px",color:"#64748b"}}>{nepaliTime(match.match_date)}</div>
          </div>
          <div style={{flexShrink:0,display:"flex",flexDirection:"column",alignItems:"flex-end",gap:"4px"}}>
            <StatusPill status={effStatus} />
            {kicked && match.status==="scheduled" && (
              <span style={{fontSize:"9px",fontWeight:700,color:"#b45309",background:"#fffbeb",padding:"1px 6px",borderRadius:"8px",border:"1px solid #fde68a"}}>
                ⏰ Kicked off
              </span>
            )}
          </div>
        </div>

        {/* ── Teams + Score block ── */}
        <div style={{display:"grid",gridTemplateColumns:"1fr auto 1fr",alignItems:"center",gap:"8px",margin:"16px 0"}}>

          {/* Home team */}
          <div style={{textAlign:"center"}}>
            <div style={{fontSize:"32px",lineHeight:1,marginBottom:"6px"}}>{flagFor(match.home_team, match.home_flag)}</div>
            <div style={{fontWeight:800,fontSize:"13px",lineHeight:1.2,wordBreak:"break-word"}}>
              {shortName(match.home_team)}
            </div>
          </div>

          {/* Score / VS */}
          <div style={{textAlign:"center",minWidth:"80px"}}>
            {hasScore ? (
              <>
                <div style={{fontWeight:900,fontSize:"30px",letterSpacing:"4px",lineHeight:1,color:"#0f172a"}}>
                  {homeScore} <span style={{color:"#cbd5e1"}}>–</span> {awayScore}
                </div>
                <div style={{fontSize:"10px",color: effStatus === "live" ? "#ef4444" : "#94a3b8",fontWeight:700,marginTop:"4px",textTransform:"uppercase",letterSpacing:"0.05em"}}>
                  {effStatus === "live" ? "🔴 Live" : "Full Time"}
                </div>
              </>
            ) : (
              <>
                <div style={{fontWeight:900,fontSize:"22px",color:"#e2e8f0",letterSpacing:"2px"}}>VS</div>
                <div style={{fontSize:"10px",color:"#94a3b8",marginTop:"4px",textTransform:"uppercase",letterSpacing:"0.05em"}}>Kickoff</div>
              </>
            )}
          </div>

          {/* Away team */}
          <div style={{textAlign:"center"}}>
            <div style={{fontSize:"32px",lineHeight:1,marginBottom:"6px"}}>{flagFor(match.away_team, match.away_flag)}</div>
            <div style={{fontWeight:800,fontSize:"13px",lineHeight:1.2,wordBreak:"break-word"}}>
              {shortName(match.away_team)}
            </div>
          </div>
        </div>

        {/* ── My prediction badge ── */}
        {myPrediction && (
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",flexWrap:"wrap",gap:"6px",background:"#f8fafc",borderRadius:"8px",padding:"8px 12px",marginBottom:"8px",border:"1px solid #e2e8f0"}}>
            <span style={{fontSize:"12px",color:"#64748b"}}>
              Your pick: <strong style={{color:"#0f172a"}}>{myPrediction.predicted_home_score} – {myPrediction.predicted_away_score}</strong>
            </span>
            <div style={{display:"flex",alignItems:"center",gap:"6px"}}>
              {(match.status === "completed" || kicked) && <OutcomePill outcome={myPrediction.scoring_reason || myPrediction.status} />}
              {(myPrediction.points_awarded > 0) && (
                <span className="pill bg-yellow-100 text-yellow-800 font-black text-xs border border-yellow-300">+{myPrediction.points_awarded} pts</span>
              )}
            </div>
          </div>
        )}

        {/* ── Admin controls ── */}
        {isAdmin && (
          <div style={{marginTop:"12px",paddingTop:"12px",borderTop:"1px dashed #e2e8f0",display:"flex",flexWrap:"wrap",gap:"8px",alignItems:"center"}}>
            <button
              style={{background:"#fbbf24",color:"#78350f",border:"none",borderRadius:"8px",padding:"6px 14px",fontWeight:800,fontSize:"12px",cursor:"pointer"}}
              onClick={() => onAction("score", match)}>
              📊 Enter Result
            </button>
            <button
              style={{background:"#f1f5f9",color:"#334155",border:"1px solid #e2e8f0",borderRadius:"8px",padding:"6px 14px",fontWeight:800,fontSize:"12px",cursor:"pointer"}}
              onClick={() => onAction("ai", match)}>
              🤖 AI Odds
            </button>
            <label style={{display:"flex",alignItems:"center",gap:"6px",fontSize:"12px",fontWeight:700,cursor:"pointer",marginLeft:"auto"}}>
              <input type="checkbox" style={{width:"15px",height:"15px",accentColor:"#059669"}}
                checked={Boolean(match.predictions_open)}
                onChange={e => onAction("togglePred", match, e.target.checked)} />
              Predictions open
            </label>
          </div>
        )}

        {/* ── User prediction form ── */}
        {canPredict && <PredictionControls match={match} existing={myPrediction} onSaved={onPredSaved} />}

        {(effStatus === "locked" || (effStatus === "live" && !hasScore)) && !isAdmin && (
          <p style={{marginTop:"8px",fontSize:"12px",fontWeight:700,color:"#b45309",background:"#fffbeb",borderRadius:"8px",padding:"8px 12px"}}>
            🔒 Predictions are locked — match has kicked off.
          </p>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  RESULT MODAL  — admin enters final score
// ─────────────────────────────────────────────────────────────────────────────
function ResultModal({ match, open, onClose, onSaved }) {
  const [home,   setHome]   = useState(0);
  const [away,   setAway]   = useState(0);
  const [mode,   setMode]   = useState("manual");
  const [saving, setSaving] = useState(false);
  const [errMsg, setErrMsg] = useState("");

  useEffect(() => {
    if (match) {
      setHome(match.home_score ?? 0);
      setAway(match.away_score ?? 0);
      setMode(match.result_mode || "manual");
    }
  }, [match?.id]);

  async function save() {
    if (!match) return;
    setSaving(true);
    setErrMsg("");
    try {
      const payload = {
        home_score:  Number(home),
        away_score:  Number(away),
        result_mode: mode,
      };
      await api.request(`/matches/${match.id}/score`, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      onSaved("✅ Result saved. Points and leaderboard updated.");
      onClose();
    } catch (err) {
      // Show error inside modal so user can see it without closing
      setErrMsg("⚠️ " + err.message);
    }
    setSaving(false);
  }

  if (!match) return null;
  return (
    <Modal open={open} onClose={onClose} title="📊 Enter Match Result">
      <div className="text-center mb-5">
        <div className="text-xs text-slate-400 mb-1 uppercase tracking-widest">{match.sport} · {match.round || "Group"}</div>
        <div className="font-black text-lg">{flagFor(match.home_team, match.home_flag)} {match.home_team} vs {flagFor(match.away_team, match.away_flag)} {match.away_team}</div>
        <div className="text-xs text-slate-500 mt-1">{nepaliTime(match.match_date)}</div>
      </div>

      <div className="flex items-center justify-center gap-5 mb-6">
        <div className="text-center">
          <div className="text-xs font-bold text-slate-400 mb-2">{flagFor(match.home_team)} {match.home_team}</div>
          <input className="input w-20 text-center font-black text-3xl py-3" type="number" min="0" max="20" value={home} onChange={e => setHome(e.target.value)} />
        </div>
        <span className="font-black text-3xl text-slate-300 mt-5">–</span>
        <div className="text-center">
          <div className="text-xs font-bold text-slate-400 mb-2">{flagFor(match.away_team)} {match.away_team}</div>
          <input className="input w-20 text-center font-black text-3xl py-3" type="number" min="0" max="20" value={away} onChange={e => setAway(e.target.value)} />
        </div>
      </div>

      <div className="mb-5">
        <label className="label">Result Source</label>
        <div className="grid grid-cols-2 gap-2">
          {["manual", "auto"].map(m => (
            <button key={m} type="button" onClick={() => setMode(m)}
              className={`btn py-2 text-sm font-black border rounded-xl ${mode === m ? "border-emerald-500 bg-emerald-50 text-emerald-800" : "border-slate-200 bg-white text-slate-500"}`}>
              {m === "manual" ? "✍️ Manual Entry" : "🤖 Auto API"}
            </button>
          ))}
        </div>
      </div>

      {errMsg && (
        <div style={{background:"#fee2e2",border:"1px solid #fca5a5",borderRadius:"8px",padding:"10px 14px",marginBottom:"12px",fontSize:"13px",fontWeight:700,color:"#991b1b"}}>
          {errMsg}
        </div>
      )}
      <button className="btn btn-primary w-full text-base" onClick={save} disabled={saving}>
        {saving ? "Saving…" : "✅ Save Result & Update Points"}
      </button>
    </Modal>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  GAMES PAGE  — matches grouped by round/stage with filters
// ─────────────────────────────────────────────────────────────────────────────
function GamesPage({ matches, user, myPredictions, onAction, onPredSaved }) {
  const [statusFilter, setStatusFilter] = useState("all");
  const [groupFilter,  setGroupFilter]  = useState("All");
  const [search, setSearch] = useState("");
  const [showCompleted, setShowCompleted] = useState(false); // collapsed by default — biggest source of scroll length

  const predMap = Object.fromEntries((myPredictions || []).map(p => [p.match_id, p]));

  // "Group" here means the round/group label (Group A, Group K, Round of 32, Final, etc.)
  const allGroups = ["All", ...Array.from(new Set(matches.map(m => m.round || "Group Stage"))).sort()];
  const statuses  = ["all", "scheduled", "live", "completed", "locked"];

  // Keeps group-stage groups (A-L) before knockout rounds, in tournament order
  const stageOrder = [
    "Group A","Group B","Group C","Group D","Group E","Group F",
    "Group G","Group H","Group I","Group J","Group K","Group L",
    "Round of 32","Round of 16","Quarter-Final","Semi-Final","3rd Place","Final",
  ];

  let filtered = matches;
  if (statusFilter !== "all") filtered = filtered.filter(m => effectiveMatchStatus(m) === statusFilter);
  if (groupFilter  !== "All") filtered = filtered.filter(m => (m.round || "Group Stage") === groupFilter);
  if (search.trim()) {
    const q = search.toLowerCase();
    filtered = filtered.filter(m =>
      m.home_team?.toLowerCase().includes(q) ||
      m.away_team?.toLowerCase().includes(q) ||
      m.stadium?.toLowerCase().includes(q)
    );
  }

  // ── Split into ACTIVE (scheduled/locked/live — needs attention) vs
  //    COMPLETED (done, archival — collapsed by default to cut scroll length) ──
  const activeMatches    = filtered.filter(m => effectiveMatchStatus(m) !== "completed");
  const completedMatches = filtered.filter(m => effectiveMatchStatus(m) === "completed");

  // ── Level 1: group by date (NPT) ──
  function buildByDate(list) {
    const byDate = {};
    list.forEach(m => {
      const key = nepaliDateKey(m.match_date);
      if (!byDate[key]) byDate[key] = [];
      byDate[key].push(m);
    });
    return byDate;
  }
  const byDateActive    = buildByDate(activeMatches);
  const byDateCompleted = buildByDate(completedMatches);

  // Active dates: soonest first (ascending — what's coming up next leads).
  // Completed dates: most recent first (descending — latest result leads).
  const sortedActiveDateKeys    = Object.keys(byDateActive).sort();
  const sortedCompletedDateKeys = Object.keys(byDateCompleted).sort().reverse();

  // Today's date key (NPT) — used to highlight "today" and to default-jump there
  const todayKey = nepaliDateKey(new Date().toISOString());

  // ── Level 2 within each date: group by round/group label, tournament order ──
  function groupWithinDate(dayMatches) {
    const grouped = {};
    dayMatches.forEach(m => {
      const r = m.round || "Group Stage";
      if (!grouped[r]) grouped[r] = [];
      grouped[r].push(m);
    });
    Object.values(grouped).forEach(g => g.sort((a, b) => new Date(a.match_date || "9999") - new Date(b.match_date || "9999")));
    return Object.entries(grouped).sort(([a], [b]) => {
      const ai = stageOrder.indexOf(a), bi = stageOrder.indexOf(b);
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    });
  }

  function jumpToDate(key) {
    const el = document.getElementById("daygroup-" + key);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // Renders one date section (used for both active and completed lists)
  function renderDateSection(dateKey, dayMatches) {
    const isToday = dateKey === todayKey;
    const dateLabel = nepaliDateLabel(dayMatches[0].match_date);
    return (
      <div key={dateKey} id={"daygroup-" + dateKey} className="space-y-4">
        {/* Date header */}
        <div className={`flex items-center gap-3 rounded-xl px-4 py-3 ${isToday ? "bg-amber-50 border border-amber-200" : "bg-slate-50 border border-slate-200"}`}>
          <span className="text-2xl">{isToday ? "📍" : "🗓️"}</span>
          <div>
            <div className="font-black text-base">
              {dateLabel} {isToday && <span className="text-amber-600 text-xs font-bold ml-1">TODAY</span>}
            </div>
            <div className="text-xs text-slate-500 font-semibold">{dayMatches.length} match{dayMatches.length !== 1 ? "es" : ""} this day</div>
          </div>
        </div>

        {/* Group-wise sub-sections within this date */}
        {groupWithinDate(dayMatches).map(([stage, ms]) => (
          <div key={stage} className="pl-2">
            <div className="flex items-center gap-3 mb-3">
              <h3 className="font-bold text-sm uppercase tracking-wide text-slate-500">⚽ {stage}</h3>
              <span className="pill bg-slate-100 text-slate-500 text-xs font-bold">{ms.length} match{ms.length !== 1 ? "es" : ""}</span>
            </div>
            <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
              {ms.map(m => (
                <MatchCard key={m.id} match={m} user={user} myPrediction={predMap[m.id]}
                  onAction={onAction} onPredSaved={onPredSaved} />
              ))}
            </div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Toolbar */}
      <div className="card p-4 flex flex-wrap gap-3 items-center">
        <input className="input max-w-xs" placeholder="🔍 Search teams or venue…" value={search} onChange={e => setSearch(e.target.value)} />
        <div className="flex gap-1 flex-wrap">
          {statuses.map(s => (
            <button key={s} onClick={() => setStatusFilter(s)}
              className={`pill text-xs font-bold cursor-pointer border transition-all ${statusFilter === s ? "bg-slate-800 text-white border-slate-800" : "bg-white text-slate-600 border-slate-200 hover:border-slate-400"}`}>
              {s === "all" ? "All" : s}
            </button>
          ))}
        </div>
      </div>

      {/* Group filter chips */}
      <div className="flex gap-2 flex-wrap">
        {allGroups.map(r => (
          <button key={r} onClick={() => setGroupFilter(r)}
            className={`pill text-xs font-bold border cursor-pointer transition-all ${groupFilter === r ? "bg-emerald-600 text-white border-emerald-600" : "bg-white text-slate-600 border-slate-200 hover:border-emerald-400"}`}>
            {r}
          </button>
        ))}
      </div>

      {/* ── Sticky date-jump bar — quick navigation across ACTIVE match days only ── */}
      {sortedActiveDateKeys.length > 1 && (
        <div className="sticky top-[120px] z-20 bg-white/95 backdrop-blur border rounded-xl shadow-sm px-3 py-2 flex gap-2 overflow-x-auto">
          {sortedActiveDateKeys.map(key => {
            const isToday = key === todayKey;
            const sample  = byDateActive[key][0];
            const label   = nepaliDateLabel(sample.match_date).replace(/, \d{4}$/, ""); // drop year for compactness
            return (
              <button key={key} onClick={() => jumpToDate(key)}
                className={`whitespace-nowrap text-xs font-bold px-3 py-1.5 rounded-lg border transition-all ${
                  isToday ? "bg-amber-500 text-white border-amber-500" : "bg-slate-50 text-slate-600 border-slate-200 hover:border-emerald-400"
                }`}>
                {isToday && "📍 "}{label}
                <span className="ml-1 opacity-70">({byDateActive[key].length})</span>
              </button>
            );
          })}
        </div>
      )}

      {/* ── ACTIVE matches — scheduled/locked/live, soonest first, always expanded ── */}
      {sortedActiveDateKeys.length
        ? sortedActiveDateKeys.map(key => renderDateSection(key, byDateActive[key]))
        : !completedMatches.length && (
          <div className="card p-12 text-center text-slate-400">
            <div className="text-4xl mb-3">🔍</div>
            <p className="font-bold">No matches found for these filters.</p>
          </div>
        )}

      {/* ── COMPLETED matches — collapsed by default, most recent first ── */}
      {completedMatches.length > 0 && (
        <div className="border-t-2 border-dashed border-slate-200 pt-5">
          <button
            onClick={() => setShowCompleted(s => !s)}
            className="w-full flex items-center justify-between gap-3 bg-slate-50 hover:bg-slate-100 border border-slate-200 rounded-xl px-4 py-3 transition-colors cursor-pointer">
            <div className="flex items-center gap-3">
              <span className="text-xl">✅</span>
              <div className="text-left">
                <div className="font-black text-sm text-slate-700">Completed Matches</div>
                <div className="text-xs text-slate-500">{completedMatches.length} finished game{completedMatches.length !== 1 ? "s" : ""} — tap to {showCompleted ? "hide" : "view"}</div>
              </div>
            </div>
            <span className="text-slate-400 font-bold text-sm">{showCompleted ? "▲ Hide" : "▼ Show"}</span>
          </button>

          {showCompleted && (
            <div className="space-y-6 mt-5">
              {sortedCompletedDateKeys.map(key => renderDateSection(key, byDateCompleted[key]))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  MY PREDICTIONS PAGE  — full table + Excel download
// ─────────────────────────────────────────────────────────────────────────────
function MyPredictionsPage({ predictions, matches, user }) {
  const [filter, setFilter] = useState("all");
  const matchMap = Object.fromEntries((matches || []).map(m => [m.id, m]));
  const withMatch = (predictions || []).map(p => ({ ...p, match: matchMap[p.match_id] })).filter(p => p.match);
  const outcome = p => p.scoring_reason || p.status || "pending";

  const filtered = filter === "all" ? withMatch : withMatch.filter(p => outcome(p) === filter);

  const totalPts = withMatch.reduce((s, p) => s + (p.points_awarded || 0), 0);
  const exact    = withMatch.filter(p => outcome(p) === "exact_score").length;
  const correct  = withMatch.filter(p => outcome(p) === "correct_winner").length;
  const wrong    = withMatch.filter(p => outcome(p) === "wrong").length;

  function exportExcel() {
    const predRows = withMatch.map(p => ({
      "Match":         p.match.home_team + " vs " + p.match.away_team,
      "Round":         p.match.round || "",
      "Date (NPT)":    nepaliTime(p.match.match_date),
      "Venue":         p.match.stadium || "",
      "My Prediction": p.predicted_home_score + "-" + p.predicted_away_score,
      "Final Score":   p.match.status === "completed" ? p.match.home_score + "-" + p.match.away_score : "Pending",
      "Outcome":       outcome(p).replace(/_/g, " "),
      "Points Earned": p.points_awarded || 0,
    }));
    const summaryRows = [
      { Metric: "Total Points",      Value: totalPts },
      { Metric: "Exact Scores",      Value: exact },
      { Metric: "Correct Outcomes",  Value: correct },
      { Metric: "Wrong Predictions", Value: wrong },
      { Metric: "Total Predictions", Value: withMatch.length },
      { Metric: "Exported On",       Value: new Date().toLocaleString() },
    ];
    downloadXLSX("WC2026_" + user.name.replace(/\s+/g, "_") + "_Predictions", [
      { name: "My Predictions", rows: predRows },
      { name: "Summary",        rows: summaryRows },
    ]);
  }

  return (
    <div className="space-y-5">
      {/* Stat bar */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <StatCard label="Total Points"       value={totalPts}          icon="🏆" accentClass="border-yellow-400" />
        <StatCard label="Exact Scores"       value={exact}             icon="🥇" accentClass="border-yellow-400" sub="×3 pts each" />
        <StatCard label="Correct Outcomes"   value={correct}           icon="✅" accentClass="border-emerald-500" sub="×1 pt each" />
        <StatCard label="Wrong Predictions"  value={wrong}             icon="❌" accentClass="border-red-400" />
        <StatCard label="Total Predictions"  value={withMatch.length}  icon="📊" accentClass="border-blue-400" />
      </div>

      {/* Filter row + export */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-1 flex-wrap">
          {["all","exact_score","correct_winner","wrong","pending"].map(f => (
            <button key={f} onClick={() => setFilter(f)}
              className={`pill text-xs font-bold border cursor-pointer transition-all ${filter === f ? "bg-slate-800 text-white border-slate-800" : "bg-white text-slate-600 border-slate-200 hover:border-slate-400"}`}>
              {f === "all" ? "All" : f.replace(/_/g, " ")}
            </button>
          ))}
        </div>
        <button onClick={exportExcel} className="btn btn-secondary text-sm font-bold">⬇️ Export to Excel</button>
      </div>

      {/* Table */}
      <div className="card overflow-hidden">
        {filtered.length ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="bg-slate-50 border-b">
                  <th className="th">#</th>
                  <th className="th">Match</th>
                  <th className="th">Round</th>
                  <th className="th">Date (NPT)</th>
                  <th className="th">My Prediction</th>
                  <th className="th">Final Score</th>
                  <th className="th">Outcome</th>
                  <th className="th">Points</th>
                  <th className="th">Download</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((p, i) => (
                  <tr key={p.id} className="border-b hover:bg-slate-50 transition-colors">
                    <td className="td text-slate-400 font-mono text-xs">{i + 1}</td>
                    <td className="td font-bold text-sm">
                      {flagFor(p.match.home_team, p.match.home_flag)} {p.match.home_team}
                      <span className="text-slate-400 mx-1">vs</span>
                      {flagFor(p.match.away_team, p.match.away_flag)} {p.match.away_team}
                    </td>
                    <td className="td text-xs text-slate-500">{p.match.round || "Group"}</td>
                    <td className="td text-xs text-slate-500 whitespace-nowrap">{nepaliTime(p.match.match_date)}</td>
                    <td className="td"><span className="font-black text-xl text-blue-700">{p.predicted_home_score} – {p.predicted_away_score}</span></td>
                    <td className="td">
                      {p.match.status === "completed"
                        ? <span className="font-black text-xl">{p.match.home_score} – {p.match.away_score}</span>
                        : <span className="text-slate-400 text-xs">Pending</span>}
                    </td>
                    <td className="td"><OutcomePill outcome={outcome(p)} /></td>
                    <td className="td">
                      <span className="font-black text-yellow-600 text-xl">
                        {outcome(p) !== "pending" ? `+${p.points_awarded || 0}` : "—"}
                      </span>
                    </td>
                    <td className="td">
                      <button
                        onClick={() => exportSingleMatch({ ...p.match, status: p.match.status }, [{ ...p, user_name: user.name, user_country: user.country }])}
                        title="Download this game's prediction as Excel"
                        style={{padding:"5px 11px",borderRadius:"6px",fontSize:"11px",fontWeight:700,cursor:"pointer",border:"1px solid #bfdbfe",background:"#dbeafe",color:"#1d4ed8",whiteSpace:"nowrap"}}>
                        ⬇️ Download
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-12 text-center text-slate-400">
            <div className="text-4xl mb-3">🎯</div>
            <p className="font-bold">No predictions match this filter.</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  LEADERBOARD PAGE — two winner types:
//    A) Per-Game Winners — best predictor for each individual completed match
//    B) Overall Leaderboard — cumulative tournament points (your original logic)
// ─────────────────────────────────────────────────────────────────────────────
function LeaderboardPage({ leaderboard, currentUser, onExport }) {
  const [tab, setTab] = useState("overall"); // "overall" | "permatch"

  return (
    <div style={{display:"flex",flexDirection:"column",gap:"16px"}}>
      {/* Tab switcher */}
      <div style={{display:"flex",gap:"8px",background:"#f1f5f9",borderRadius:"12px",padding:"4px",width:"fit-content"}}>
        <button onClick={() => setTab("overall")}
          style={{padding:"8px 18px",borderRadius:"9px",fontSize:"13px",fontWeight:800,cursor:"pointer",border:"none",
            background: tab==="overall" ? "#fff" : "transparent",
            color: tab==="overall" ? "#0f172a" : "#64748b",
            boxShadow: tab==="overall" ? "0 1px 3px rgba(0,0,0,.1)" : "none"}}>
          🏆 Overall Leaderboard
        </button>
        <button onClick={() => setTab("permatch")}
          style={{padding:"8px 18px",borderRadius:"9px",fontSize:"13px",fontWeight:800,cursor:"pointer",border:"none",
            background: tab==="permatch" ? "#fff" : "transparent",
            color: tab==="permatch" ? "#0f172a" : "#64748b",
            boxShadow: tab==="permatch" ? "0 1px 3px rgba(0,0,0,.1)" : "none"}}>
          🎯 Per-Game Winners
        </button>
      </div>

      {tab === "overall"
        ? <OverallLeaderboardTable leaderboard={leaderboard} currentUser={currentUser} onExport={onExport} />
        : <PerGameWinners currentUser={currentUser} />}
    </div>
  );
}

// ── TYPE B: Overall tournament leaderboard (your original component body) ──
function OverallLeaderboardTable({ leaderboard, currentUser, onExport }) {
  // Only show users who have at least 1 prediction (points > 0 OR predictions exist)
  const active = leaderboard.filter(l => (l.predictions_count || l.total_predictions || l.points > 0 || l.accuracy > 0));
  const display = active.length ? active : leaderboard; // fallback: show all if filter removes everyone

  return (
    <div style={{display:"flex",flexDirection:"column",gap:"16px"}}>
      {/* Scoring legend */}
      <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",flexWrap:"wrap",gap:"10px"}}>
        <div style={{display:"flex",gap:"16px",flexWrap:"wrap",background:"#fff",border:"1px solid #e2e8f0",borderRadius:"12px",padding:"12px 18px",fontSize:"13px",color:"#475569"}}>
          <span>🥇 <strong>Exact Score</strong> = 3 pts</span>
          <span>✅ <strong>Correct Outcome</strong> = 1 pt</span>
          <span>❌ <strong>Wrong</strong> = 0 pts</span>
          <span style={{color:"#94a3b8",borderLeft:"1px solid #e2e8f0",paddingLeft:"16px",fontSize:"12px"}}>
            Showing <strong style={{color:"#059669"}}>{display.length}</strong> active participant{display.length !== 1 ? "s" : ""} with predictions
          </span>
        </div>
        <button onClick={onExport}
          style={{padding:"9px 18px",borderRadius:"9px",border:"1.5px solid #e2e8f0",background:"#fff",fontSize:"13px",fontWeight:700,cursor:"pointer",color:"#334155"}}>
          ⬇️ Export Leaderboard
        </button>
      </div>

      {/* Table */}
      <div style={{background:"#fff",border:"1px solid #e2e8f0",borderRadius:"14px",overflow:"hidden",boxShadow:"0 1px 3px rgba(0,0,0,.06)"}}>
        <div style={{overflowX:"auto"}}>
          <table style={{width:"100%",borderCollapse:"collapse"}}>
            <thead>
              <tr style={{background:"#f8fafc",borderBottom:"1px solid #e2e8f0"}}>
                {["Rank","Player","Country","Points","Accuracy","Predictions","Badges"].map(h => (
                  <th key={h} style={{padding:"10px 14px",fontSize:"10px",fontWeight:800,textTransform:"uppercase",letterSpacing:"0.09em",color:"#94a3b8",textAlign:"left",whiteSpace:"nowrap"}}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {display.map((l, i) => {
                const isMe  = l.id === currentUser.id;
                const medal = ["🥇","🥈","🥉"][i] || "";
                const rankColors = ["#d97706","#64748b","#92400e"];
                return (
                  <tr key={l.id} style={{borderBottom:"1px solid #f1f5f9",background:isMe?"#f0fdf4":i%2===0?"#fff":"#fafafa",transition:"background .15s"}}>
                    <td style={{padding:"12px 14px",textAlign:"center"}}>
                      {medal
                        ? <span style={{fontSize:"22px"}}>{medal}</span>
                        : <span style={{fontWeight:800,fontSize:"14px",color:rankColors[i]||"#94a3b8"}}>#{i+1}</span>}
                    </td>
                    <td style={{padding:"12px 14px"}}>
                      <div style={{display:"flex",alignItems:"center",gap:"10px"}}>
                        <div style={{
                          width:"36px",height:"36px",borderRadius:"50%",flexShrink:0,
                          background:isMe?"linear-gradient(135deg,#10b981,#059669)":"linear-gradient(135deg,#94a3b8,#64748b)",
                          display:"flex",alignItems:"center",justifyContent:"center",
                          color:"#fff",fontWeight:800,fontSize:"14px",
                        }}>
                          {l.name?.[0]?.toUpperCase()}
                        </div>
                        <div>
                          <div style={{fontWeight:700,fontSize:"14px",display:"flex",alignItems:"center",gap:"6px"}}>
                            {l.name}
                            {isMe && <span style={{padding:"1px 8px",background:"#d1fae5",color:"#065f46",borderRadius:"10px",fontSize:"10px",fontWeight:800,border:"1px solid #6ee7b7"}}>You</span>}
                          </div>
                          <div style={{fontSize:"11px",color:"#94a3b8"}}>{l.email || ""}</div>
                        </div>
                      </div>
                    </td>
                    <td style={{padding:"12px 14px",fontSize:"12px",color:"#64748b"}}>{l.country || "—"}</td>
                    <td style={{padding:"12px 14px"}}>
                      <span style={{fontWeight:900,fontSize:"24px",color:"#d97706"}}>{l.points}</span>
                    </td>
                    <td style={{padding:"12px 14px"}}>
                      <div style={{display:"flex",alignItems:"center",gap:"6px"}}>
                        <div style={{flex:1,height:"5px",background:"#f1f5f9",borderRadius:"3px",minWidth:"60px"}}>
                          <div style={{height:"100%",borderRadius:"3px",background:"linear-gradient(90deg,#10b981,#059669)",width:(l.accuracy||0)+"%"}}/>
                        </div>
                        <span style={{fontWeight:700,fontSize:"12px",color:"#059669"}}>{l.accuracy||0}%</span>
                      </div>
                    </td>
                    <td style={{padding:"12px 14px",textAlign:"center"}}>
                      <span style={{fontWeight:700,fontSize:"14px",color:"#3b82f6"}}>{l.predictions_count||l.total_predictions||"—"}</span>
                    </td>
                    <td style={{padding:"12px 14px",fontSize:"13px"}}>{l.badges||"—"}</td>
                  </tr>
                );
              })}
              {!display.length && (
                <tr><td colSpan={7} style={{textAlign:"center",padding:"48px",color:"#94a3b8",fontWeight:700}}>No participants yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ── TYPE A: Per-game winners — best predictor for each completed match ──
function PerGameWinners({ currentUser }) {
  const [data,    setData]    = useState({ matches: [], total_completed_matches: 0 });
  const [loading, setLoading] = useState(true);
  const [search,  setSearch]  = useState("");

  async function load() {
    setLoading(true);
    try {
      const r = await api.request("/predictions/match-winners");
      setData(r);
    } catch {
      setData({ matches: [], total_completed_matches: 0 });
    }
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  const q = search.toLowerCase().trim();
  const filtered = (data.matches || []).filter(m =>
    !q ||
    (m.home_team || "").toLowerCase().includes(q) ||
    (m.away_team || "").toLowerCase().includes(q) ||
    (m.game_no   || "").toLowerCase().includes(q) ||
    (m.round     || "").toLowerCase().includes(q)
  );

  return (
    <div style={{display:"flex",flexDirection:"column",gap:"16px"}}>
      <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",flexWrap:"wrap",gap:"10px"}}>
        <div style={{display:"flex",gap:"10px",alignItems:"center",flexWrap:"wrap"}}>
          <input
            style={{padding:"8px 12px",border:"1.5px solid #e2e8f0",borderRadius:"8px",fontSize:"13px",outline:"none",width:"220px"}}
            placeholder="🔍 Search team, game no, round…"
            value={search} onChange={e => setSearch(e.target.value)}
          />
          <span style={{fontSize:"12px",fontWeight:700,color:"#94a3b8"}}>
            {filtered.length} completed match{filtered.length !== 1 ? "es" : ""}
          </span>
        </div>
        <button onClick={load} disabled={loading}
          style={{padding:"8px 16px",borderRadius:"8px",border:"1.5px solid #e2e8f0",background:loading?"#f8fafc":"#fff",
            fontSize:"12px",fontWeight:700,cursor:loading?"not-allowed":"pointer",color:"#334155"}}>
          {loading ? "⏳ Loading…" : "↻ Refresh"}
        </button>
      </div>

      {loading && (
        <div style={{textAlign:"center",padding:"48px",color:"#94a3b8",fontSize:"14px",fontWeight:600}}>Loading per-game winners…</div>
      )}

      {!loading && filtered.length === 0 && (
        <div style={{textAlign:"center",padding:"60px 20px",color:"#94a3b8"}}>
          <div style={{fontSize:"48px",marginBottom:"14px"}}>🎯</div>
          <p style={{fontWeight:700,fontSize:"14px"}}>No completed matches yet.</p>
          <p style={{fontSize:"12px",marginTop:"6px"}}>Per-game winners appear here once results are entered for matches you've predicted.</p>
        </div>
      )}

      {!loading && filtered.map(m => {
        const isTied = m.winners.length > 1;
        return (
          <div key={m.match_id} style={{background:"#fff",border:"1px solid #e2e8f0",borderRadius:"14px",overflow:"hidden",boxShadow:"0 1px 4px rgba(0,0,0,.05)"}}>
            <div style={{height:"4px",background:"#10b981"}} />
            <div style={{padding:"14px 18px"}}>
              <div style={{display:"flex",alignItems:"flex-start",justifyContent:"space-between",gap:"12px",flexWrap:"wrap"}}>
                <div>
                  <div style={{fontSize:"11px",fontWeight:800,color:"#94a3b8",textTransform:"uppercase",letterSpacing:"0.07em",marginBottom:"6px"}}>
                    {m.game_no} · {m.round} · {nepaliTime(m.match_date)}
                  </div>
                  <div style={{display:"flex",alignItems:"center",gap:"10px"}}>
                    <span style={{fontWeight:800,fontSize:"16px"}}>{flagFor(m.home_team)} {m.home_team}</span>
                    <span style={{fontWeight:900,fontSize:"18px",letterSpacing:"2px",background:"#f1f5f9",padding:"2px 10px",borderRadius:"8px"}}>
                      {m.home_score} – {m.away_score}
                    </span>
                    <span style={{fontWeight:800,fontSize:"16px"}}>{flagFor(m.away_team)} {m.away_team}</span>
                  </div>
                </div>
                <div style={{fontSize:"11px",color:"#64748b",fontWeight:600,textAlign:"right"}}>
                  {m.total_participants} participant{m.total_participants !== 1 ? "s" : ""}
                </div>
              </div>

              {/* Winner(s) */}
              <div style={{marginTop:"12px",display:"flex",flexDirection:"column",gap:"6px"}}>
                {isTied && (
                  <div style={{fontSize:"11px",fontWeight:700,color:"#92400e",background:"#fffbeb",border:"1px solid #fde68a",borderRadius:"8px",padding:"4px 10px",width:"fit-content"}}>
                    🤝 Tied — {m.winners.length} players matched the top score
                  </div>
                )}
                {m.winners.map(w => {
                  const isMe = w.user_id === currentUser.id;
                  return (
                    <div key={w.user_id} style={{
                      display:"flex",alignItems:"center",gap:"10px",
                      background: isMe ? "#f0fdf4" : "#fffbeb",
                      border:"1px solid " + (isMe ? "#86efac" : "#fde68a"),
                      borderRadius:"10px",padding:"8px 14px",
                    }}>
                      <span style={{fontSize:"18px"}}>🏅</span>
                      <div style={{display:"flex",alignItems:"center",gap:"6px",flex:1}}>
                        <span style={{fontWeight:800,fontSize:"14px"}}>{w.user_name}</span>
                        {isMe && <span style={{padding:"1px 7px",background:"#d1fae5",color:"#065f46",borderRadius:"10px",fontSize:"10px",fontWeight:800}}>You</span>}
                        <span style={{fontSize:"11px",color:"#64748b"}}>{w.user_country || ""}</span>
                      </div>
                      <span style={{fontWeight:900,fontSize:"15px",letterSpacing:"2px",color:"#2563eb"}}>
                        {w.predicted_home_score}–{w.predicted_away_score}
                      </span>
                      <span style={{fontWeight:900,fontSize:"16px",color:"#d97706"}}>+{w.points_awarded}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  ADMIN — ALL PREDICTIONS VIEW
// ─────────────────────────────────────────────────────────────────────────────
function AdminAllPredictions({ matches, onExport, onRefresh }) {
  const [allPreds, setAllPreds] = useState([]);
  const [loading,  setLoading]  = useState(true);
  const [search,   setSearch]   = useState("");
  const [msg,      setMsg]      = useState("");
  const [emailingMatchId, setEmailingMatchId] = useState(null);

  const matchMap = Object.fromEntries((matches || []).map(m => [m.id, m]));

  async function load() {
    setLoading(true);
    try {
      const data = await api.request("/predictions/admin/all");
      setAllPreds(Array.isArray(data) ? data : (data.predictions || []));
    } catch { setAllPreds([]); }
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  async function emailMatchParticipants(matchId, match) {
    setEmailingMatchId(matchId);
    try {
      const r = await api.request(`/admin/notify/match-participants/${matchId}`, { method: "POST" });
      const label = (match?.home_team || "?") + " vs " + (match?.away_team || "?");
      setMsg(`📧 ${label} — sent: ${r.sent}, skipped: ${r.skipped}, failed: ${r.failed}.`);
    } catch (e) {
      setMsg("⚠️ " + e.message);
    }
    setEmailingMatchId(null);
  }

  async function lockPrediction(p) {
    try {
      await api.request("/predictions/" + p.id + "/lock", { method: "PUT" });
      setMsg("🔒 Prediction locked for " + (p.user_name || "user"));
      load();
    } catch(e) {
      // Fallback: lock via match prediction-status
      try {
        await api.request("/matches/" + p.match_id + "/prediction-status", { method: "PUT", body: JSON.stringify({ predictions_open: false }) });
        setMsg("🔒 Match predictions locked.");
        if (onRefresh) onRefresh();
        load();
      } catch { setMsg("⚠️ Lock not supported by backend yet."); }
    }
  }

  async function deletePrediction(p) {
    if (!window.confirm("Delete prediction by " + (p.user_name || "this user") + "?")) return;
    try {
      await api.request("/predictions/" + p.id, { method: "DELETE" });
      setMsg("🗑️ Prediction deleted.");
      load();
    } catch(e) { setMsg("⚠️ " + e.message); }
  }

  const filtered = allPreds.filter(p => {
    const m = matchMap[p.match_id] || {};
    const q = search.toLowerCase();
    return !q ||
      (p.user_name||"").toLowerCase().includes(q) ||
      (m.home_team||"").toLowerCase().includes(q) ||
      (m.away_team||"").toLowerCase().includes(q);
  });

  // Group by match for clear display
  const byMatch = {};
  filtered.forEach(p => {
    const mid = p.match_id;
    if (!byMatch[mid]) byMatch[mid] = [];
    byMatch[mid].push(p);
  });

  return (
    <div style={{display:"flex",flexDirection:"column",gap:"16px"}}>
      {/* Toolbar */}
      <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",flexWrap:"wrap",gap:"10px"}}>
        <div style={{display:"flex",alignItems:"center",gap:"10px",flexWrap:"wrap"}}>
          <input
            style={{padding:"8px 12px",border:"1.5px solid #e2e8f0",borderRadius:"8px",fontSize:"13px",outline:"none",width:"220px"}}
            placeholder="🔍 Search user or team…"
            value={search} onChange={e => setSearch(e.target.value)}
          />
          <span style={{fontSize:"13px",fontWeight:700,color:"#64748b"}}>{filtered.length} prediction{filtered.length!==1?"s":""}</span>
        </div>
        <div style={{display:"flex",gap:"8px"}}>
          <button onClick={load} style={{padding:"8px 16px",borderRadius:"8px",border:"1.5px solid #e2e8f0",background:"#fff",fontSize:"12px",fontWeight:700,cursor:"pointer"}}>↻ Refresh</button>
          <button onClick={() => onExport(allPreds, matchMap)} style={{padding:"8px 16px",borderRadius:"8px",border:"1.5px solid #e2e8f0",background:"#fff",fontSize:"12px",fontWeight:700,cursor:"pointer"}}>⬇️ Export Excel</button>
        </div>
      </div>

      {msg && <div style={{padding:"10px 16px",borderRadius:"8px",background:"#f0fdf4",border:"1px solid #bbf7d0",fontSize:"13px",fontWeight:700,color:"#065f46"}}>{msg}</div>}

      {loading ? (
        <div style={{padding:"48px",textAlign:"center",color:"#94a3b8",fontSize:"14px"}}>Loading predictions…</div>
      ) : Object.keys(byMatch).length === 0 ? (
        <div style={{padding:"48px",textAlign:"center",color:"#94a3b8",fontSize:"14px",fontWeight:700}}>No predictions found.</div>
      ) : Object.entries(byMatch).map(([mid, preds]) => {
        const m = matchMap[mid] || {};
        return (
          <div key={mid} style={{background:"#fff",border:"1px solid #e2e8f0",borderRadius:"12px",overflow:"hidden"}}>
            {/* Match header */}
            <div style={{
              background:"#f8fafc",padding:"12px 16px",borderBottom:"1px solid #e2e8f0",
              display:"flex",alignItems:"center",justifyContent:"space-between",flexWrap:"wrap",gap:"8px"
            }}>
              <div>
                <span style={{fontWeight:800,fontSize:"14px"}}>
                  {flagFor(m.home_team)} {m.home_team||"?"} vs {flagFor(m.away_team)} {m.away_team||"?"}
                </span>
                <span style={{marginLeft:"10px",fontSize:"11px",color:"#64748b"}}>{m.game_no||""} · {m.round||""} · {nepaliTime(m.match_date)}</span>
              </div>
              <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
                <span style={{
                  padding:"3px 10px",borderRadius:"20px",fontSize:"11px",fontWeight:700,
                  background:"#dbeafe",color:"#1d4ed8",
                }}>👥 {preds.length} participant{preds.length!==1?"s":""}</span>
                {m.status==="completed" && <span style={{fontWeight:800,fontSize:"14px",color:"#0f172a"}}>{m.home_score}–{m.away_score}</span>}
                <StatusPill status={m.status}/>
                {/* Admin: export just this match's predictions to Excel */}
                <button
                  onClick={() => exportSingleMatch(m, preds)}
                  style={{padding:"4px 12px",borderRadius:"6px",fontSize:"11px",fontWeight:700,cursor:"pointer",border:"1px solid #bfdbfe",background:"#dbeafe",color:"#1d4ed8"}}>
                  ⬇️ Export This Game
                </button>
                {/* Admin: email just this match's participants */}
                <button
                  onClick={() => emailMatchParticipants(mid, m)}
                  disabled={emailingMatchId === mid}
                  style={{padding:"4px 12px",borderRadius:"6px",fontSize:"11px",fontWeight:700,
                    cursor:emailingMatchId===mid?"not-allowed":"pointer",
                    border:"1px solid #c4b5fd",background: emailingMatchId===mid ? "#ede9fe" : "#f5f3ff",color:"#7c3aed"}}>
                  {emailingMatchId === mid ? "⏳ Sending…" : "📧 Email Participants"}
                </button>
                {/* Admin: lock all predictions for this match */}
                <button
                  onClick={() => lockPrediction({match_id:mid, user_name:"all users"})}
                  style={{padding:"4px 12px",borderRadius:"6px",fontSize:"11px",fontWeight:700,cursor:"pointer",border:"1px solid #fde68a",background:"#fef9c3",color:"#92400e"}}>
                  🔒 Lock All
                </button>
              </div>
            </div>

            {/* Predictions rows */}
            <div style={{overflowX:"auto"}}>
              <table style={{width:"100%",borderCollapse:"collapse"}}>
                <thead>
                  <tr style={{borderBottom:"1px solid #f1f5f9"}}>
                    {["#","Player","Country","Predicted","Final","Outcome","Points","Submitted","Actions"].map(h=>(
                      <th key={h} style={{padding:"8px 12px",fontSize:"10px",fontWeight:800,textTransform:"uppercase",letterSpacing:"0.08em",color:"#94a3b8",textAlign:"left",whiteSpace:"nowrap"}}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preds.map((p,i) => {
                    const o = p.scoring_reason || p.status || "pending";
                    const outcomeStyle = {
                      exact_score:    {bg:"#fef9c3",color:"#854d0e",label:"🥇 Exact"},
                      correct_winner: {bg:"#dcfce7",color:"#166534",label:"✅ Correct"},
                      wrong:          {bg:"#fee2e2",color:"#991b1b",label:"❌ Wrong"},
                      pending:        {bg:"#f1f5f9",color:"#64748b",label:"⏳ Pending"},
                    }[o] || {bg:"#f1f5f9",color:"#64748b",label:"⏳ Pending"};
                    return (
                      <tr key={p.id} style={{borderBottom:"1px solid #f8fafc",background:i%2===0?"#fff":"#fafafa"}}>
                        <td style={{padding:"10px 12px",fontSize:"12px",color:"#94a3b8",fontFamily:"monospace"}}>{i+1}</td>
                        <td style={{padding:"10px 12px"}}>
                          <div style={{display:"flex",alignItems:"center",gap:"7px"}}>
                            <div style={{width:"28px",height:"28px",borderRadius:"50%",background:"linear-gradient(135deg,#94a3b8,#64748b)",display:"flex",alignItems:"center",justifyContent:"center",color:"#fff",fontWeight:800,fontSize:"11px",flexShrink:0}}>
                              {(p.name||p.user_name||"?")[0].toUpperCase()}
                            </div>
                            <div>
                              <div style={{fontWeight:700,fontSize:"13px"}}>{p.name||p.user_name||("User #"+p.user_id)}</div>
                              <div style={{fontSize:"10px",color:"#94a3b8"}}>{p.email||""}</div>
                            </div>
                          </div>
                        </td>
                        <td style={{padding:"10px 12px",fontSize:"12px",color:"#64748b"}}>{p.user_country||p.country||"—"}</td>
                        <td style={{padding:"10px 12px"}}>
                          <span style={{fontWeight:900,fontSize:"18px",letterSpacing:"2px",color:"#2563eb"}}>{p.predicted_home_score}–{p.predicted_away_score}</span>
                        </td>
                        <td style={{padding:"10px 12px"}}>
                          {m.status==="completed"
                            ? <span style={{fontWeight:900,fontSize:"18px",letterSpacing:"2px"}}>{m.home_score}–{m.away_score}</span>
                            : <span style={{fontSize:"11px",color:"#94a3b8"}}>Pending</span>}
                        </td>
                        <td style={{padding:"10px 12px"}}>
                          <span style={{padding:"3px 9px",borderRadius:"12px",fontSize:"11px",fontWeight:700,background:outcomeStyle.bg,color:outcomeStyle.color}}>
                            {outcomeStyle.label}
                          </span>
                        </td>
                        <td style={{padding:"10px 12px"}}>
                          <span style={{fontWeight:900,fontSize:"17px",color:"#d97706"}}>
                            {o!=="pending"?"+"+( p.points_awarded||0):"—"}
                          </span>
                        </td>
                        <td style={{padding:"10px 12px",fontSize:"11px",color:"#94a3b8",whiteSpace:"nowrap"}}>
                          {p.created_at?new Date(p.created_at).toLocaleString():"—"}
                        </td>
                        <td style={{padding:"10px 12px"}}>
                          <div style={{display:"flex",gap:"5px"}}>
                            <button onClick={() => lockPrediction(p)}
                              style={{padding:"4px 10px",borderRadius:"6px",fontSize:"11px",fontWeight:700,cursor:"pointer",border:"1px solid #fde68a",background:"#fef9c3",color:"#92400e"}}>
                              🔒 Lock
                            </button>
                            <button onClick={() => deletePrediction(p)}
                              style={{padding:"4px 10px",borderRadius:"6px",fontSize:"11px",fontWeight:700,cursor:"pointer",border:"1px solid #fecaca",background:"#fee2e2",color:"#991b1b"}}>
                              🗑️ Delete
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  ADMIN — USERS TABLE
// ─────────────────────────────────────────────────────────────────────────────
function AdminUsersPage({ users, onRefresh, onMessage, currentUser }) {
  const [search,      setSearch]      = useState("");
  const [filterRole,  setFilterRole]  = useState("all");
  const [filterStatus,setFilterStatus]= useState("all");
  const [confirmId,   setConfirmId]   = useState(null); // user id pending delete confirm

  const filtered = (users || []).filter(u => {
    const q = search.toLowerCase();
    const matchQ = !q || u.name?.toLowerCase().includes(q) || u.email?.toLowerCase().includes(q) || u.mobile?.toLowerCase().includes(q);
    const matchRole   = filterRole   === "all" || u.role === filterRole;
    const matchStatus = filterStatus === "all" || (filterStatus === "active" ? u.is_active : !u.is_active);
    return matchQ && matchRole && matchStatus;
  });

  const activeCount   = (users||[]).filter(u => u.is_active).length;
  const inactiveCount = (users||[]).filter(u => !u.is_active).length;

  async function toggleActive(u) {
    try {
      await api.request(`/admin/users/${u.id}/toggle-active`, { method: "PUT" });
      onMessage(u.is_active ? `🔒 ${u.name} deactivated.` : `✅ ${u.name} activated.`);
      onRefresh();
    } catch (err) { onMessage("⚠️ " + err.message); }
  }

  async function deleteUser(u) {
    try {
      await api.request(`/admin/users/${u.id}`, { method: "DELETE" });
      onMessage(`🗑️ ${u.name} deleted.`);
      setConfirmId(null);
      onRefresh();
    } catch (err) { onMessage("⚠️ " + err.message); setConfirmId(null); }
  }

  async function resetPassword(u) {
    const newPass = prompt(`Set new password for ${u.name}:`);
    if (!newPass || newPass.length < 6) { onMessage("⚠️ Password must be at least 6 characters."); return; }
    try {
      await api.request(`/admin/users/${u.id}/reset-password`, { method: "PUT", body: JSON.stringify({ password: newPass }) });
      onMessage(`🔑 Password reset for ${u.name}.`);
    } catch (err) { onMessage("⚠️ " + err.message); }
  }

  return (
    <div style={{display:"flex",flexDirection:"column",gap:"16px"}}>

      {/* ── KPI row ── */}
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(120px,1fr))",gap:"12px"}}>
        {[
          { icon:"👥", label:"Total Users",    val:(users||[]).length,   col:"#1d4ed8", bg:"#eff6ff", brd:"#bfdbfe" },
          { icon:"✅", label:"Active",         val:activeCount,          col:"#059669", bg:"#f0fdf4", brd:"#86efac" },
          { icon:"🔒", label:"Inactive",       val:inactiveCount,        col:"#92400e", bg:"#fffbeb", brd:"#fde68a" },
          { icon:"🛡️", label:"Admins",         val:(users||[]).filter(u=>u.role==="admin").length, col:"#b91c1c", bg:"#fef2f2", brd:"#fca5a5" },
        ].map(k=>(
          <div key={k.label} style={{background:k.bg,border:"1px solid "+k.brd,borderRadius:"12px",padding:"12px 14px"}}>
            <div style={{fontSize:"18px",marginBottom:"3px"}}>{k.icon}</div>
            <div style={{fontWeight:900,fontSize:"24px",color:k.col,lineHeight:1}}>{k.val}</div>
            <div style={{fontSize:"10px",fontWeight:700,color:k.col,textTransform:"uppercase",letterSpacing:"0.07em",marginTop:"3px",opacity:.8}}>{k.label}</div>
          </div>
        ))}
      </div>

      {/* ── Filters ── */}
      <div style={{background:"#fff",border:"1px solid #e2e8f0",borderRadius:"12px",padding:"12px 16px",display:"flex",gap:"10px",flexWrap:"wrap",alignItems:"center"}}>
        <input
          style={{padding:"8px 12px",border:"1.5px solid #e2e8f0",borderRadius:"8px",fontSize:"13px",outline:"none",flex:1,minWidth:"180px"}}
          placeholder="🔍 Search name, email, mobile…"
          value={search} onChange={e=>setSearch(e.target.value)}
        />
        {["all","user","admin"].map(r=>(
          <button key={r} onClick={()=>setFilterRole(r)}
            style={{padding:"5px 12px",borderRadius:"20px",fontSize:"11px",fontWeight:700,cursor:"pointer",border:"1.5px solid",
              borderColor:filterRole===r?"#0f172a":"#e2e8f0",
              background:filterRole===r?"#0f172a":"#fff",
              color:filterRole===r?"#fff":"#64748b"}}>
            {r==="all"?"All Roles":r}
          </button>
        ))}
        {["all","active","inactive"].map(s=>(
          <button key={s} onClick={()=>setFilterStatus(s)}
            style={{padding:"5px 12px",borderRadius:"20px",fontSize:"11px",fontWeight:700,cursor:"pointer",border:"1.5px solid",
              borderColor:filterStatus===s?"#059669":"#e2e8f0",
              background:filterStatus===s?"#059669":"#fff",
              color:filterStatus===s?"#fff":"#64748b"}}>
            {s==="all"?"All Status":s}
          </button>
        ))}
        <span style={{fontSize:"12px",fontWeight:700,color:"#94a3b8",marginLeft:"auto"}}>{filtered.length} user{filtered.length!==1?"s":""}</span>
      </div>

      {/* ── Delete confirm banner ── */}
      {confirmId && (() => {
        const u = (users||[]).find(x=>x.id===confirmId);
        return u ? (
          <div style={{background:"#fee2e2",border:"1px solid #fca5a5",borderRadius:"10px",padding:"14px 18px",display:"flex",alignItems:"center",gap:"12px",flexWrap:"wrap"}}>
            <span style={{fontSize:"18px"}}>⚠️</span>
            <div style={{flex:1}}>
              <div style={{fontWeight:800,fontSize:"13px",color:"#991b1b"}}>Delete {u.name}?</div>
              <div style={{fontSize:"11px",color:"#b91c1c",marginTop:"2px"}}>
                This will permanently delete the user and ALL their predictions. This cannot be undone.
              </div>
            </div>
            <button onClick={()=>deleteUser(u)}
              style={{padding:"7px 16px",borderRadius:"8px",background:"#dc2626",color:"#fff",border:"none",fontWeight:800,fontSize:"12px",cursor:"pointer"}}>
              Yes, Delete
            </button>
            <button onClick={()=>setConfirmId(null)}
              style={{padding:"7px 16px",borderRadius:"8px",background:"#fff",color:"#64748b",border:"1px solid #e2e8f0",fontWeight:800,fontSize:"12px",cursor:"pointer"}}>
              Cancel
            </button>
          </div>
        ) : null;
      })()}

      {/* ── Users table ── */}
      <div style={{background:"#fff",border:"1px solid #e2e8f0",borderRadius:"14px",overflow:"hidden"}}>
        <div style={{overflowX:"auto"}}>
          <table style={{width:"100%",borderCollapse:"collapse"}}>
            <thead>
              <tr style={{background:"#f8fafc",borderBottom:"1px solid #e2e8f0"}}>
                {["ID","Name","Email","Mobile","Country","Role","Status","Joined","Predictions","Actions"].map(h=>(
                  <th key={h} style={{padding:"10px 12px",fontSize:"10px",fontWeight:800,textTransform:"uppercase",letterSpacing:"0.08em",color:"#94a3b8",textAlign:"left",whiteSpace:"nowrap"}}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((u,i) => {
                const isSelf = u.id === currentUser?.id;
                return (
                  <tr key={u.id} style={{borderBottom:"1px solid #f1f5f9",background:isSelf?"#f0fdf4":i%2===0?"#fff":"#fafafa"}}>
                    <td style={{padding:"10px 12px",fontSize:"11px",color:"#94a3b8",fontFamily:"monospace"}}>{u.id}</td>
                    <td style={{padding:"10px 12px"}}>
                      <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
                        <div style={{
                          width:"30px",height:"30px",borderRadius:"50%",flexShrink:0,
                          background:u.role==="admin"?"linear-gradient(135deg,#ef4444,#dc2626)":"linear-gradient(135deg,#94a3b8,#64748b)",
                          display:"flex",alignItems:"center",justifyContent:"center",color:"#fff",fontWeight:800,fontSize:"12px"
                        }}>
                          {(u.name||"?")[0].toUpperCase()}
                        </div>
                        <div>
                          <div style={{fontWeight:700,fontSize:"13px",display:"flex",alignItems:"center",gap:"5px"}}>
                            {u.name}
                            {isSelf&&<span style={{padding:"1px 6px",background:"#d1fae5",color:"#065f46",borderRadius:"8px",fontSize:"9px",fontWeight:800}}>You</span>}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td style={{padding:"10px 12px",fontSize:"11px",color:"#64748b"}}>{u.email||"—"}</td>
                    <td style={{padding:"10px 12px",fontSize:"11px",color:"#64748b"}}>{u.mobile||"—"}</td>
                    <td style={{padding:"10px 12px",fontSize:"11px"}}>{u.country||"—"}</td>
                    <td style={{padding:"10px 12px"}}>
                      <span style={{padding:"2px 9px",borderRadius:"20px",fontSize:"10px",fontWeight:700,
                        background:u.role==="admin"?"#fee2e2":"#dbeafe",
                        color:u.role==="admin"?"#991b1b":"#1d4ed8"}}>
                        {u.role}
                      </span>
                    </td>
                    <td style={{padding:"10px 12px"}}>
                      <span style={{padding:"2px 9px",borderRadius:"20px",fontSize:"10px",fontWeight:700,
                        background:u.is_active?"#d1fae5":"#f1f5f9",
                        color:u.is_active?"#065f46":"#94a3b8",
                        border:"1px solid "+(u.is_active?"#6ee7b7":"#e2e8f0")}}>
                        {u.is_active?"● Active":"○ Inactive"}
                      </span>
                    </td>
                    <td style={{padding:"10px 12px",fontSize:"11px",color:"#94a3b8",whiteSpace:"nowrap"}}>
                      {u.created_at?fmtDateShort(u.created_at):"—"}
                    </td>
                    <td style={{padding:"10px 12px",fontSize:"12px",fontWeight:700,color:"#3b82f6",textAlign:"center"}}>
                      {u.predictions_count||"—"}
                    </td>
                    <td style={{padding:"10px 12px"}}>
                      <div style={{display:"flex",gap:"5px",flexWrap:"wrap"}}>
                        {/* Activate / Deactivate */}
                        <button onClick={()=>toggleActive(u)}
                          style={{padding:"4px 10px",borderRadius:"6px",fontSize:"11px",fontWeight:700,cursor:"pointer",border:"1px solid",
                            borderColor:u.is_active?"#fde68a":"#86efac",
                            background:u.is_active?"#fef9c3":"#dcfce7",
                            color:u.is_active?"#92400e":"#065f46"}}>
                          {u.is_active?"🔒 Deactivate":"✅ Activate"}
                        </button>
                        {/* Reset password */}
                        {!isSelf && (
                          <button onClick={()=>resetPassword(u)}
                            style={{padding:"4px 10px",borderRadius:"6px",fontSize:"11px",fontWeight:700,cursor:"pointer",
                              border:"1px solid #bfdbfe",background:"#eff6ff",color:"#1d4ed8"}}>
                            🔑 Reset PW
                          </button>
                        )}
                        {/* Delete — disabled for self */}
                        {!isSelf && (
                          <button onClick={()=>setConfirmId(u.id)}
                            style={{padding:"4px 10px",borderRadius:"6px",fontSize:"11px",fontWeight:700,cursor:"pointer",
                              border:"1px solid #fca5a5",background:"#fee2e2",color:"#dc2626"}}>
                            🗑️ Delete
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
              {!filtered.length&&(
                <tr><td colSpan={10} style={{textAlign:"center",padding:"40px",color:"#94a3b8",fontWeight:700}}>No users match this filter.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  DATA RESET PANEL
// ─────────────────────────────────────────────────────────────────────────────
function DataResetPanel({ onMessage }) {
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);

  const ACTIONS = [
    {
      id:    "predictions",
      icon:  "🎯",
      label: "Clear All Predictions",
      desc:  "Deletes every user's prediction across all matches. Users and matches stay intact.",
      color: "#92400e", bg:"#fffbeb", brd:"#fde68a",
      endpoint: "/admin/reset/predictions",
    },
    {
      id:    "matches",
      icon:  "⚽",
      label: "Clear All Matches & Teams",
      desc:  "Removes all scheduled, live, and completed matches plus teams. Tournaments stay.",
      color: "#1d4ed8", bg:"#eff6ff", brd:"#bfdbfe",
      endpoint: "/admin/reset/matches",
    },
    {
      id:    "tournaments",
      icon:  "🏆",
      label: "Clear All Tournaments",
      desc:  "Removes all tournaments, teams, matches, and predictions. Users stay.",
      color: "#7c3aed", bg:"#f5f3ff", brd:"#c4b5fd",
      endpoint: "/admin/reset/tournaments",
    },
    {
      id:    "leaderboard",
      icon:  "📊",
      label: "Reset Leaderboard",
      desc:  "Clears all leaderboard points and rankings. Predictions stay but points reset to 0.",
      color: "#065f46", bg:"#f0fdf4", brd:"#86efac",
      endpoint: "/admin/reset/leaderboard",
    },
  ];

  async function doReset(action) {
    if (confirm !== action.label) {
      onMessage("⚠️ Type the exact label to confirm.");
      return;
    }
    setLoading(true);
    try {
      const r = await api.request(action.endpoint, { method: "DELETE" });
      onMessage("✅ " + (r.message || action.label + " completed."));
      setConfirm("");
    } catch (err) {
      onMessage("⚠️ " + err.message);
    }
    setLoading(false);
  }

  return (
    <div style={{background:"#fff",border:"1px solid #e2e8f0",borderRadius:"12px",padding:"20px"}}>
      <div style={{marginBottom:"16px"}}>
        <h3 style={{fontWeight:800,fontSize:"13px",textTransform:"uppercase",letterSpacing:"0.08em",color:"#94a3b8"}}>
          🗑️ Data Reset
        </h3>
        <p style={{fontSize:"12px",color:"#64748b",marginTop:"4px"}}>
          Selectively clear game data. <strong>Users are never deleted here.</strong> Type the exact button label to confirm each action.
        </p>
      </div>

      <div style={{marginBottom:"14px"}}>
        <input
          style={{width:"100%",padding:"9px 12px",border:"1.5px solid #e2e8f0",borderRadius:"8px",fontSize:"13px",outline:"none"}}
          placeholder="Type the action label exactly to confirm (e.g. Clear All Predictions)"
          value={confirm}
          onChange={e => setConfirm(e.target.value)}
        />
      </div>

      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(240px,1fr))",gap:"10px"}}>
        {ACTIONS.map(a => (
          <div key={a.id} style={{background:a.bg,border:"1px solid "+a.brd,borderRadius:"10px",padding:"14px"}}>
            <div style={{fontSize:"20px",marginBottom:"4px"}}>{a.icon}</div>
            <div style={{fontWeight:800,fontSize:"13px",color:a.color,marginBottom:"4px"}}>{a.label}</div>
            <div style={{fontSize:"11px",color:"#64748b",marginBottom:"12px",lineHeight:1.5}}>{a.desc}</div>
            <button
              onClick={() => doReset(a)}
              disabled={loading || confirm !== a.label}
              style={{
                width:"100%",padding:"7px 14px",borderRadius:"7px",
                border:"1px solid "+a.brd,
                background: confirm === a.label ? a.color : "#fff",
                color:       confirm === a.label ? "#fff"   : a.color,
                fontWeight:800,fontSize:"12px",cursor: confirm===a.label?"pointer":"not-allowed",
                opacity: loading ? .5 : 1,
                transition:"all .2s",
              }}>
              {loading && confirm === a.label ? "⏳ Processing…" : a.label}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  ADMIN — AUTO RESULT POST PANEL
//  Two-step flow: 1) Check Results (preview, nothing saved) 2) Post (admin
//  confirms each result, or bulk-posts everything marked ready).
//  Pulls from football-data.org via /api/auto-result/* (auto_push_result.py).
// ─────────────────────────────────────────────────────────────────────────────
function AutoResultPostPanel({ selected, onRefresh }) {
  const [checking,  setChecking]  = useState(false);
  const [posting,   setPosting]   = useState(null); // match_id currently posting, or "all"
  const [results,   setResults]   = useState(null); // null = not checked yet
  const [error,     setError]     = useState("");

  async function checkResults() {
    if (!selected) { setError("Select a tournament first."); return; }
    setChecking(true);
    setError("");
    try {
      const r = await api.request(`/auto-result/check/${selected}`);
      setResults(r);
    } catch (e) {
      setError(e.message);
      setResults(null);
    }
    setChecking(false);
  }

  async function postOne(result) {
    setPosting(result.match_id);
    setError("");
    try {
      await api.request(`/auto-result/post/${result.match_id}?home_score=${result.home_score}&away_score=${result.away_score}`, { method: "POST" });
      if (onRefresh) await onRefresh(`✅ Posted ${result.home_team} ${result.home_score}-${result.away_score} ${result.away_team}`);
      await checkResults(); // refresh the list so posted matches drop off
    } catch (e) {
      setError(e.message);
    }
    setPosting(null);
  }

  async function postAllReady() {
    if (!selected) return;
    setPosting("all");
    setError("");
    try {
      const r = await api.request(`/auto-result/post-all/${selected}`, { method: "POST" });
      if (onRefresh) await onRefresh(`✅ Posted ${r.posted_count} result${r.posted_count !== 1 ? "s" : ""} automatically.`);
      await checkResults();
    } catch (e) {
      setError(e.message);
    }
    setPosting(null);
  }

  const ready   = (results?.results || []).filter(r => r.ready_to_post);
  const notYet  = (results?.results || []).filter(r => r.found && !r.ready_to_post);
  const noMatch = (results?.results || []).filter(r => !r.found);

  return (
    <div style={{display:"flex",flexDirection:"column",gap:"18px"}}>
      {/* Intro / explainer */}
      <div style={{background:"#eff6ff",border:"1px solid #bfdbfe",borderRadius:"12px",padding:"14px 18px"}}>
        <div style={{fontWeight:800,fontSize:"14px",color:"#1e3a8a",marginBottom:"4px"}}>📡 Auto Result Post</div>
        <div style={{fontSize:"12px",color:"#1e40af",lineHeight:1.6}}>
          Checks football-data.org for results matching your scheduled, locked, or live matches.
          Nothing is posted automatically — review each match below and click <strong>Post Result</strong> to confirm,
          or <strong>Post All Ready Results</strong> to confirm everything found at once.
        </div>
      </div>

      {/* Toolbar */}
      <div style={{display:"flex",gap:"10px",flexWrap:"wrap",alignItems:"center"}}>
        <button onClick={checkResults} disabled={checking || !selected}
          style={{padding:"10px 20px",borderRadius:"9px",border:"none",
            background: checking ? "#93c5fd" : "linear-gradient(135deg,#2563eb,#1d4ed8)",
            color:"#fff",fontWeight:800,fontSize:"13px",cursor:checking?"not-allowed":"pointer"}}>
          {checking ? "⏳ Checking…" : "🔍 Check Results"}
        </button>

        {ready.length > 0 && (
          <button onClick={postAllReady} disabled={posting !== null}
            style={{padding:"10px 20px",borderRadius:"9px",border:"none",
              background: posting === "all" ? "#86efac" : "linear-gradient(135deg,#16a34a,#15803d)",
              color:"#fff",fontWeight:800,fontSize:"13px",cursor:posting?"not-allowed":"pointer"}}>
            {posting === "all" ? "⏳ Posting…" : `✅ Post All Ready Results (${ready.length})`}
          </button>
        )}

        {results && (
          <span style={{fontSize:"12px",fontWeight:700,color:"#64748b"}}>
            Checked {results.checked} match{results.checked !== 1 ? "es" : ""}
          </span>
        )}
      </div>

      {error && (
        <div style={{padding:"12px 16px",borderRadius:"10px",background:"#fef2f2",border:"1px solid #fca5a5",color:"#b91c1c",fontSize:"13px",fontWeight:700}}>
          ⚠️ {error}
        </div>
      )}

      {/* Not yet checked */}
      {!results && !checking && (
        <div style={{textAlign:"center",padding:"50px 20px",color:"#94a3b8"}}>
          <div style={{fontSize:"40px",marginBottom:"10px"}}>📡</div>
          <p style={{fontWeight:700,fontSize:"14px"}}>Click "Check Results" to scan for available match results.</p>
        </div>
      )}

      {/* Ready to post */}
      {ready.length > 0 && (
        <div>
          <div style={{fontWeight:800,fontSize:"13px",color:"#166534",marginBottom:"8px"}}>✅ Ready to Post ({ready.length})</div>
          <div style={{display:"flex",flexDirection:"column",gap:"8px"}}>
            {ready.map(r => (
              <div key={r.match_id} style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:"12px",
                background:"#f0fdf4",border:"1px solid #86efac",borderRadius:"10px",padding:"12px 16px",flexWrap:"wrap"}}>
                <div>
                  <div style={{fontWeight:800,fontSize:"14px"}}>
                    {flagFor(r.home_team)} {r.home_team} <span style={{color:"#16a34a",fontWeight:900,letterSpacing:"2px"}}>{r.home_score}–{r.away_score}</span> {flagFor(r.away_team)} {r.away_team}
                  </div>
                  <div style={{fontSize:"11px",color:"#64748b",marginTop:"2px"}}>
                    {r.game_no} · {r.round} · matched: {r.matched_as} ({r.confidence}% confidence)
                  </div>
                </div>
                <button onClick={() => postOne(r)} disabled={posting !== null}
                  style={{padding:"7px 16px",borderRadius:"8px",border:"none",
                    background: posting === r.match_id ? "#86efac" : "#16a34a",
                    color:"#fff",fontWeight:700,fontSize:"12px",cursor:posting?"not-allowed":"pointer",whiteSpace:"nowrap"}}>
                  {posting === r.match_id ? "⏳ Posting…" : "✅ Post Result"}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Found but not finished yet (live/scheduled on the API side) */}
      {notYet.length > 0 && (
        <div>
          <div style={{fontWeight:800,fontSize:"13px",color:"#92400e",marginBottom:"8px"}}>🔄 Found, Not Finished Yet ({notYet.length})</div>
          <div style={{display:"flex",flexDirection:"column",gap:"6px"}}>
            {notYet.map(r => (
              <div key={r.match_id} style={{background:"#fffbeb",border:"1px solid #fde68a",borderRadius:"10px",padding:"10px 16px",fontSize:"12px",color:"#92400e"}}>
                <strong>{r.home_team} vs {r.away_team}</strong> — matched as {r.matched_as}, status: {r.fixture_status}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* No confident match found */}
      {noMatch.length > 0 && (
        <div>
          <div style={{fontWeight:800,fontSize:"13px",color:"#64748b",marginBottom:"8px"}}>❔ No Match Found on football-data.org ({noMatch.length})</div>
          <div style={{display:"flex",flexDirection:"column",gap:"6px"}}>
            {noMatch.map(r => (
              <div key={r.match_id} style={{background:"#f8fafc",border:"1px solid #e2e8f0",borderRadius:"10px",padding:"10px 16px",fontSize:"12px",color:"#64748b"}}>
                <strong>{r.home_team} vs {r.away_team}</strong> ({r.game_no}) — {r.message}
              </div>
            ))}
          </div>
        </div>
      )}

      {results && ready.length === 0 && notYet.length === 0 && noMatch.length === 0 && (
        <div style={{textAlign:"center",padding:"40px",color:"#94a3b8",fontWeight:700,fontSize:"13px"}}>
          No scheduled, locked, or live matches to check right now.
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  ADMIN — REPORTS & SETTINGS PANEL  (your original logic preserved)
// ─────────────────────────────────────────────────────────────────────────────
function AdminReportsPanel({ selected, users, onMessage }) {
  const [selectAll,     setSelectAll]     = useState(true);
  const [selectedUsers, setSelectedUsers] = useState([]);
  const [regs,          setRegs]          = useState({ email_required: true, mobile_required: false });
  const [loading,       setLoading]       = useState(false);

  useEffect(() => { api.request("/admin/registration-settings").then(setRegs).catch(() => {}); }, []);
  function toggleUser(id) { setSelectedUsers(c => c.includes(id) ? c.filter(x => x !== id) : [...c, id]); }

  async function exportReports() {
    setLoading(true);
    try {
      const r = await api.request(`/admin/exports/${selected}`, { method: "POST" });
      onMessage("✅ Reports generated: " + Object.values(r).join(", "));
    } catch (err) { onMessage("⚠️ " + err.message); }
    setLoading(false);
  }

  async function sendEmails() {
    setLoading(true);
    try {
      const r = await api.request("/admin/email-reports", {
        method: "POST",
        body: JSON.stringify({
          select_all: selectAll,
          user_ids: selectAll ? [] : selectedUsers,
          subject: "WorldCup 2026 results and predictions",
          message: "Latest result, predictions, and leaderboard Excel reports are attached.",
        }),
      });
      onMessage(`📧 Email done — sent: ${r.sent}, skipped: ${r.skipped}, failed: ${r.failed}. Recipients: ${r.recipients}`);
    } catch (err) { onMessage("⚠️ " + err.message); }
    setLoading(false);
  }

  async function saveRegs() {
    try {
      await api.request("/admin/registration-settings", { method: "PUT", body: JSON.stringify(regs) });
      onMessage("✅ Registration settings saved.");
    } catch (err) { onMessage("⚠️ " + err.message); }
  }

  return (
    <div className="space-y-5">
      {/* Excel export */}
      <div className="card p-5">
        <h3 className="font-black text-sm uppercase tracking-widest text-slate-400 mb-1">📥 Generate Excel Reports</h3>
        <p className="text-sm text-slate-600 mb-4">Creates predictions.xlsx, results.xlsx, and leaderboard.xlsx for the selected tournament.</p>
        <button onClick={exportReports} disabled={loading} className="btn btn-primary">{loading ? "Generating…" : "Generate Reports"}</button>
      </div>

      {/* Email reports */}
      <div className="card p-5">
        <div className="flex items-start justify-between flex-wrap gap-3 mb-3">
          <div>
            <h3 className="font-black text-sm uppercase tracking-widest text-slate-400">📧 Email Reports to Users</h3>
            <p className="text-sm text-slate-600 mt-1">Sends predictions, results, and leaderboard Excel files after results are completed.</p>
          </div>
          <button onClick={sendEmails} disabled={loading} className="btn btn-secondary font-bold">{loading ? "Sending…" : "Send Email Reports"}</button>
        </div>
        <label className="flex items-center gap-2 font-bold cursor-pointer mb-3">
          <input type="checkbox" className="w-4 h-4 accent-emerald-600" checked={selectAll} onChange={e => setSelectAll(e.target.checked)} />
          Select all active users
        </label>
        {!selectAll && (
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {(users || []).map(u => (
              <label key={u.id} className="border rounded-xl p-2 text-sm flex gap-2 cursor-pointer hover:border-emerald-400 transition-colors">
                <input type="checkbox" checked={selectedUsers.includes(u.id)} onChange={() => toggleUser(u.id)} />
                <span><strong>{u.name}</strong><br /><span className="text-slate-400 text-xs">{u.email}</span></span>
              </label>
            ))}
          </div>
        )}
      </div>

      {/* Registration settings */}
      <div className="card p-5">
        <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
          <div>
            <h3 className="font-black text-sm uppercase tracking-widest text-slate-400">⚙️ Registration Requirements</h3>
            <p className="text-xs text-slate-500 mt-1">Control which fields are mandatory at sign-up.</p>
          </div>
          <button onClick={saveRegs} className="btn btn-primary text-sm">Save Settings</button>
        </div>
        <div className="grid sm:grid-cols-2 gap-3">
          <label className="border rounded-xl p-3 font-bold flex gap-2 cursor-pointer hover:border-emerald-400 transition-colors">
            <input type="checkbox" className="w-4 h-4 accent-emerald-600"
              checked={regs.email_required} onChange={e => setRegs(r => ({ ...r, email_required: e.target.checked }))} />
            Email address mandatory
          </label>
          <label className="border rounded-xl p-3 font-bold flex gap-2 cursor-pointer hover:border-emerald-400 transition-colors">
            <input type="checkbox" className="w-4 h-4 accent-emerald-600"
              checked={regs.mobile_required} onChange={e => setRegs(r => ({ ...r, mobile_required: e.target.checked }))} />
            Mobile number mandatory
          </label>
        </div>
      </div>

      {/* ── Data Reset ── */}
      <DataResetPanel onMessage={onMessage} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  PREDICTION LIST PAGE
//  Visible to ALL users. Shows every participant's prediction per match.
//  RULE: predictions are HIDDEN while match is "scheduled" (open for betting).
//        They become VISIBLE once status = locked | live | completed.
//        This prevents bias — no one can see others' picks before the game.
// ─────────────────────────────────────────────────────────────────────────────
function PredictionListPage({ matches, currentUser, myPredictions, leaderboard }) {
  const isAdmin = currentUser.role === "admin";

  const [allPreds,      setAllPreds]      = useState([]);
  const [loadingPreds,  setLoadingPreds]  = useState(true);
  const [search,        setSearch]        = useState("");
  const [expandedMatch, setExpandedMatch] = useState(null);
  const [debugMsg,      setDebugMsg]      = useState("");

  // Match IDs this user personally predicted in
  const myMatchIds = new Set((myPredictions || []).map(p => p.match_id));

  // Uses global effectiveMatchStatus + kickoffPassed helpers
  function isVisible(match) {
    if (isAdmin) return true;
    const eff = effectiveMatchStatus(match);
    return ["locked","live","completed"].includes(eff);
  }

  // ── Normalise any API shape into flat prediction array ──
  function normalise(raw, fallbackMatchId) {
    let arr = [];
    if (Array.isArray(raw))                                   arr = raw;
    else if (Array.isArray(raw && raw.predictions))           arr = raw.predictions;
    else if (Array.isArray(raw && raw.data))                  arr = raw.data;
    else if (Array.isArray(raw && raw.items))                 arr = raw.items;
    else if (raw && typeof raw === "object")
      arr = Object.values(raw).find(v => Array.isArray(v)) || [];

    return arr.map(p => ({
      ...p,
      match_id:             Number(p.match_id     || p.matchId       || fallbackMatchId || 0),
      user_id:              p.user_id              || p.userId        || null,
      // your backend joins users and returns `name` and `email` (not user_name)
      user_name:            p.user_name            || p.userName      || p.name          || "Unknown",
      user_email:           p.user_email           || p.email         || "",
      user_country:         p.user_country         || p.userCountry   || p.country       || "",
      predicted_home_score: p.predicted_home_score ?? p.home_score_prediction ?? p.home  ?? "?",
      predicted_away_score: p.predicted_away_score ?? p.away_score_prediction ?? p.away  ?? "?",
      points_awarded:       p.points_awarded       ?? p.points        ?? 0,
      // your backend scoring uses these field names
      scoring_reason:       p.scoring_reason       || p.result_label  || p.outcome       || (p.status === "completed" ? "pending" : p.status) || "pending",
    }));
  }

  const fetchPredictions = useCallback(async function () {
    setLoadingPreds(true);
    setDebugMsg("");
    let combined = [];

    // ══════════════════════════════════════════════
    //  ADMIN — fetch ALL predictions
    // ══════════════════════════════════════════════
    if (isAdmin) {
      // Try 1: /admin/predictions
      try {
        const raw = await api.request("/predictions/admin/all");
        const arr = normalise(raw, null);
        arr.forEach(p => combined.push(p));
        setDebugMsg("✅ Source: /predictions/admin/all → " + arr.length + " rows");
      } catch (e) {
        setDebugMsg("⚠️ /predictions/admin/all error: " + e.message);
      }

      // Try 2: per-match for every match
      if (!combined.length) {
        const settled = await Promise.allSettled(
          matches.map(m => api.request("/predictions/match/" + m.id))
        );
        settled.forEach((r, i) => {
          if (r.status === "fulfilled")
            normalise(r.value, matches[i].id).forEach(p => combined.push(p));
        });
        if (combined.length) setDebugMsg("Source: per-match → " + combined.length + " rows");
      }

      // Try 3: /predictions/all
      if (!combined.length) {
        try {
          const raw = await api.request("/predictions/admin/all");
          normalise(raw, null).forEach(p => combined.push(p));
          if (combined.length) setDebugMsg("Source: /predictions/all → " + combined.length + " rows");
        } catch {}
      }

      if (!combined.length) setDebugMsg("⚠️ All endpoints returned 0 rows — check /predictions/admin/all in your backend");

    // ══════════════════════════════════════════════
    //  USER — only visible matches they predicted in
    // ══════════════════════════════════════════════
    } else {
      const relevant = matches.filter(m => isVisible(m) && myMatchIds.has(m.id));

      // Try per-match
      const settled = await Promise.allSettled(
        relevant.map(m => api.request("/predictions/match/" + m.id))
      );
      settled.forEach((r, i) => {
        if (r.status === "fulfilled")
          normalise(r.value, relevant[i].id).forEach(p => combined.push(p));
      });

      // Fallback: admin endpoint filtered
      if (!combined.length) {
        try {
          const raw = await api.request("/predictions/admin/all");
          normalise(raw, null)
            .filter(p => myMatchIds.has(p.match_id))
            .forEach(p => combined.push(p));
        } catch {}
      }

      // Always include own predictions so user sees themselves
      const myKeys = new Set(combined.map(p => p.match_id + "|" + (p.user_id || p.user_name)));
      (myPredictions || []).forEach(p => {
        const key = p.match_id + "|" + (currentUser.id || currentUser.name);
        if (!myKeys.has(key)) {
          combined.push({
            ...p,
            user_id:      currentUser.id,
            user_name:    p.user_name    || currentUser.name,
            user_country: p.user_country || currentUser.country || "",
          });
        }
      });
    }

    // Deduplicate
    const seen = new Set();
    const deduped = combined.filter(p => {
      const k = (p.match_id || "") + "|" + (p.user_id || p.user_name || "");
      if (seen.has(k)) return false;
      seen.add(k); return true;
    });

    console.log("[PredictionList] loaded", deduped.length, "predictions", deduped);
    setAllPreds(deduped);
    setLoadingPreds(false);
  }, [matches.length, (myPredictions || []).length]);

  useEffect(() => {
    if (!matches.length) return;
    fetchPredictions();
  }, [matches.length, (myPredictions || []).length]);

  // Auto-expand first match after data loads
  useEffect(() => {
    if (loadingPreds || !allPreds.length || expandedMatch) return;
    const grouped = {};
    allPreds.forEach(p => { if (!grouped[p.match_id]) grouped[p.match_id] = []; grouped[p.match_id].push(p); });
    const first = matches.find(m => (grouped[m.id] || []).length > 0);
    if (first) setExpandedMatch(first.id);
  }, [loadingPreds]);

  // Build predsByMatch
  const predsByMatch = {};
  allPreds.forEach(p => {
    const mid = p.match_id;
    if (!predsByMatch[mid]) predsByMatch[mid] = [];
    predsByMatch[mid].push(p);
  });

  // Which matches to display
  const relevantMatches = isAdmin ? matches : matches.filter(m => myMatchIds.has(m.id));
  const searchQ = search.toLowerCase().trim();
  const displayMatches = relevantMatches
    .filter(m =>
      !searchQ ||
      (m.home_team || "").toLowerCase().includes(searchQ) ||
      (m.away_team || "").toLowerCase().includes(searchQ) ||
      (m.stadium   || "").toLowerCase().includes(searchQ) ||
      (m.game_no   || "").toLowerCase().includes(searchQ)
    )
    .sort((a, b) => {
      const order = { live:0, locked:1, completed:2, scheduled:3 };
      return (order[a.status] ?? 9) - (order[b.status] ?? 9);
    });

  // KPI numbers
  const totalPreds       = allPreds.length;
  const uniqueUsers      = new Set(allPreds.map(p => p.user_id || p.user_name)).size;
  const completedCount   = relevantMatches.filter(m => m.status === "completed").length;
  const liveCount        = relevantMatches.filter(m => m.status === "live").length;
  const lockedCount      = relevantMatches.filter(m => m.status === "locked").length;
  const scheduledCount   = relevantMatches.filter(m => m.status === "scheduled").length;
  const myPredCount      = (myPredictions || []).length;

  return (
    <div>

      {/* ── KPI CARDS ── */}
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(130px,1fr))",gap:"12px",marginBottom:"20px"}}>
        {(isAdmin ? [
          { icon:"📊", label:"Total Predictions", val:totalPreds,     col:"#1d4ed8", bg:"#eff6ff", brd:"#bfdbfe" },
          { icon:"👥", label:"Unique Users",       val:uniqueUsers,   col:"#065f46", bg:"#f0fdf4", brd:"#86efac" },
          { icon:"✅", label:"Completed",          val:completedCount, col:"#059669", bg:"#f0fdf4", brd:"#6ee7b7" },
          { icon:"🔴", label:"Live Now",           val:liveCount,     col:"#b91c1c", bg:"#fef2f2", brd:"#fca5a5" },
          { icon:"🔒", label:"Locked",             val:lockedCount,   col:"#92400e", bg:"#fffbeb", brd:"#fde68a" },
          { icon:"📅", label:"Scheduled",          val:scheduledCount, col:"#7c3aed", bg:"#f5f3ff", brd:"#c4b5fd" },
        ] : [
          { icon:"🎯", label:"My Predictions",    val:myPredCount,   col:"#1d4ed8", bg:"#eff6ff", brd:"#bfdbfe" },
          { icon:"⚽", label:"My Matches",        val:relevantMatches.length, col:"#7c3aed", bg:"#f5f3ff", brd:"#c4b5fd" },
          { icon:"✅", label:"Completed",         val:completedCount, col:"#059669", bg:"#f0fdf4", brd:"#6ee7b7" },
          { icon:"🔒", label:"Hidden",            val:scheduledCount, col:"#92400e", bg:"#fffbeb", brd:"#fde68a" },
        ]).map(k => (
          <div key={k.label} style={{background:k.bg,border:"1px solid "+k.brd,borderRadius:"12px",padding:"14px 16px"}}>
            <div style={{fontSize:"20px",marginBottom:"4px"}}>{k.icon}</div>
            <div style={{fontWeight:900,fontSize:"26px",color:k.col,lineHeight:1}}>{k.val}</div>
            <div style={{fontSize:"10px",fontWeight:700,color:k.col,textTransform:"uppercase",letterSpacing:"0.07em",marginTop:"4px",opacity:.8}}>{k.label}</div>
          </div>
        ))}
      </div>

      {/* ── Admin info / debug bar ── */}
      {isAdmin && debugMsg && (
        <div style={{background:"#f0fdf4",border:"1px solid #bbf7d0",borderRadius:"8px",padding:"8px 14px",marginBottom:"12px",fontSize:"12px",color:"#065f46",fontWeight:600,display:"flex",alignItems:"center",gap:"8px"}}>
          🛡️ {debugMsg}
        </div>
      )}

      {/* ── Search + Refresh ── */}
      <div style={{background:"#fff",border:"1px solid #e2e8f0",borderRadius:"12px",padding:"12px 16px",marginBottom:"16px",display:"flex",gap:"10px",alignItems:"center",flexWrap:"wrap"}}>
        <input
          style={{padding:"8px 12px",border:"1.5px solid #e2e8f0",borderRadius:"8px",fontSize:"13px",outline:"none",flex:1,minWidth:"180px"}}
          placeholder="🔍 Search team, game no, venue…"
          value={search} onChange={e => setSearch(e.target.value)}
        />
        <span style={{fontSize:"12px",fontWeight:700,color:"#94a3b8"}}>{displayMatches.length} match{displayMatches.length!==1?"es":""}</span>
        <button onClick={fetchPredictions} disabled={loadingPreds}
          style={{padding:"8px 16px",borderRadius:"8px",border:"1.5px solid #e2e8f0",
            background:loadingPreds?"#f8fafc":"#fff",fontSize:"12px",fontWeight:700,
            cursor:loadingPreds?"not-allowed":"pointer",color:"#334155"}}>
          {loadingPreds?"⏳ Loading…":"↻ Refresh"}
        </button>
      </div>

      {loadingPreds && (
        <div style={{textAlign:"center",padding:"60px",color:"#94a3b8",fontSize:"14px",fontWeight:600}}>Loading predictions…</div>
      )}

      {!loadingPreds && displayMatches.length===0 && (
        <div style={{textAlign:"center",padding:"60px 20px",color:"#94a3b8"}}>
          <div style={{fontSize:"48px",marginBottom:"14px"}}>🎯</div>
          <p style={{fontWeight:700,fontSize:"14px"}}>{isAdmin?"No matches found.":"You haven't predicted any matches yet."}</p>
          {!isAdmin&&<p style={{fontSize:"12px",marginTop:"6px"}}>Go to <strong>Games</strong> tab to submit predictions.</p>}
        </div>
      )}

      {/* ── Match Cards ── */}
      {!loadingPreds && displayMatches.map(match => {
        const preds  = predsByMatch[match.id] || [];
        const kicked = kickoffPassed(match);
        // RULE: predictions stay hidden ONLY while the match is "scheduled"
        // (predictions still open, kickoff hasn't happened). As soon as a
        // match is locked, live, or completed, every participant can see
        // everyone else's pick for that match — not just after the final
        // result is entered. This matches the backend rule in
        // GET /predictions/match/{match_id}.
        const effStatus = effectiveMatchStatus(match);
        const isRevealed = ["locked","live","completed"].includes(effStatus);
        const isCompleted = effStatus === "completed";
        const hidden = !isAdmin && !isRevealed;
        const isOpen = expandedMatch === match.id;
        const myPred = (myPredictions||[]).find(p => p.match_id===match.id);
        const outcome= myPred ? (myPred.scoring_reason||myPred.status||"pending") : null;

        const OS = {
          exact_score:    {bg:"#fef9c3",color:"#854d0e",border:"#fde047",label:"🥇 Exact"},
          correct_winner: {bg:"#dcfce7",color:"#166534",border:"#86efac",label:"✅ Correct"},
          wrong:          {bg:"#fee2e2",color:"#991b1b",border:"#fca5a5",label:"❌ Wrong"},
          pending:        {bg:"#f1f5f9",color:"#64748b",border:"#e2e8f0",label:"⏳ Pending"},
        };
        const myOS = OS[outcome] || OS.pending;

        // Effective status label — if kickoff passed but DB still says scheduled
        const effectiveStatus = effectiveMatchStatus(match);
        const stripColor = effectiveStatus==="live"?"#ef4444":effectiveStatus==="completed"?"#10b981":effectiveStatus==="locked"?"#f59e0b":"#93c5fd";

        return (
          <div key={match.id} style={{background:"#fff",border:"1px solid #e2e8f0",borderRadius:"14px",marginBottom:"12px",overflow:"hidden",boxShadow:"0 1px 4px rgba(0,0,0,.05)"}}>
            <div style={{height:"4px",background:stripColor}}/>

            {/* Header */}
            <div onClick={()=>!hidden&&setExpandedMatch(isOpen?null:match.id)}
              style={{padding:"14px 18px",cursor:hidden?"default":"pointer",userSelect:"none"}}>
              <div style={{display:"flex",alignItems:"flex-start",justifyContent:"space-between",gap:"12px",flexWrap:"wrap"}}>

                {/* Left */}
                <div style={{flex:1,minWidth:0}}>
                  <div style={{display:"flex",alignItems:"center",gap:"8px",marginBottom:"6px",flexWrap:"wrap"}}>
                    <StatusPill status={effectiveStatus}/>
                    {kicked && match.status==="scheduled" && (
                      <span style={{padding:"2px 8px",borderRadius:"10px",fontSize:"10px",fontWeight:700,background:"#fef3c7",color:"#92400e",border:"1px solid #fde68a"}}>
                        ⏰ Kickoff passed
                      </span>
                    )}
                    <span style={{fontSize:"11px",fontWeight:800,color:"#94a3b8",textTransform:"uppercase",letterSpacing:"0.07em"}}>
                      {match.game_no||("#"+match.id)} · {match.sport||"FIFA"} · {match.round||"Group"}
                    </span>
                  </div>

                  {/* Teams + score */}
                  <div style={{display:"flex",alignItems:"center",gap:"10px",flexWrap:"wrap",marginBottom:"6px"}}>
                    <span style={{fontWeight:800,fontSize:"17px"}}>{flagFor(match.home_team,match.home_flag)} {shortName(match.home_team)}</span>
                    {(match.status==="completed"||match.status==="live") ? (
                      <span style={{fontWeight:900,fontSize:"20px",letterSpacing:"3px",background:match.status==="live"?"#fee2e2":"#f1f5f9",color:match.status==="live"?"#b91c1c":"#0f172a",padding:"2px 12px",borderRadius:"8px"}}>
                        {match.home_score??0} – {match.away_score??0}
                      </span>
                    ):(
                      <span style={{fontWeight:900,fontSize:"14px",color:"#cbd5e1",letterSpacing:"2px"}}>VS</span>
                    )}
                    <span style={{fontWeight:800,fontSize:"17px"}}>{flagFor(match.away_team,match.away_flag)} {shortName(match.away_team)}</span>
                  </div>

                  <div style={{display:"flex",gap:"14px",flexWrap:"wrap"}}>
                    <span style={{fontSize:"11px",color:"#64748b"}}>📍 {match.stadium||"Venue TBD"}</span>
                    <span style={{fontSize:"11px",color:"#64748b"}}>🕐 {nepaliTime(match.match_date)}</span>
                  </div>
                </div>

                {/* Right — KPI + my pick */}
                <div style={{display:"flex",flexDirection:"column",alignItems:"flex-end",gap:"6px",flexShrink:0}}>
                  {/* Total predictions KPI — shown once revealed (locked/live/completed), or always for admin */}
                  {!hidden && (
                    <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
                      <div style={{display:"flex",alignItems:"center",gap:"6px",background:"#f0fdf4",border:"1px solid #86efac",borderRadius:"20px",padding:"5px 14px"}}>
                        <span style={{fontSize:"14px"}}>👥</span>
                        <div style={{textAlign:"center"}}>
                          <div style={{fontWeight:900,fontSize:"20px",color:"#065f46",lineHeight:1}}>{preds.length}</div>
                          <div style={{fontSize:"9px",fontWeight:700,color:"#059669",textTransform:"uppercase",letterSpacing:"0.06em"}}>
                            Prediction{preds.length!==1?"s":""}
                          </div>
                        </div>
                      </div>
                      {/* Download this game's predictions — same export every user can use, not just admin */}
                      <button
                        onClick={(e) => { e.stopPropagation(); exportSingleMatch(match, preds); }}
                        title="Download this game's predictions as Excel"
                        style={{padding:"7px 12px",borderRadius:"8px",fontSize:"11px",fontWeight:700,cursor:"pointer",border:"1px solid #bfdbfe",background:"#dbeafe",color:"#1d4ed8",whiteSpace:"nowrap"}}>
                        ⬇️ Download
                      </button>
                    </div>
                  )}

                  {/* Unique users (admin only) */}
                  {isAdmin && preds.length>0 && (
                    <div style={{fontSize:"10px",color:"#64748b",fontWeight:600}}>
                      {new Set(preds.map(p=>p.user_id||p.user_name)).size} unique user{new Set(preds.map(p=>p.user_id||p.user_name)).size!==1?"s":""}
                    </div>
                  )}

                  {/* My pick (user only) */}
                  {!isAdmin && myPred && (
                    <div style={{display:"flex",alignItems:"center",gap:"5px",background:myOS.bg,border:"1px solid "+myOS.border,borderRadius:"10px",padding:"4px 10px"}}>
                      <span style={{fontSize:"11px",color:"#64748b",fontWeight:600}}>My pick:</span>
                      <span style={{fontWeight:900,fontSize:"15px",letterSpacing:"2px",color:"#2563eb"}}>{myPred.predicted_home_score}–{myPred.predicted_away_score}</span>
                      {outcome&&outcome!=="pending"&&<span style={{fontSize:"10px",fontWeight:700,color:myOS.color}}>{myOS.label}</span>}
                    </div>
                  )}

                  {!hidden&&(
                    <span style={{fontSize:"11px",fontWeight:700,color:"#3b82f6",cursor:"pointer"}}>
                      {isOpen?"▲ Collapse":"▼ View All"}
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* Hidden notice */}
            {hidden && (
              <div style={{margin:"0 18px 14px",background:"#fffbeb",border:"1px solid #fde68a",borderRadius:"10px",padding:"10px 14px",display:"flex",gap:"8px",alignItems:"center"}}>
                <span>🔒</span>
                <div style={{fontSize:"12px",color:"#92400e",fontWeight:600}}>
                  Your pick is saved. Everyone's predictions for this match will be revealed once predictions close (the match is locked) — you don't need to wait for the final result.
                </div>
              </div>
            )}

            {/* Admin notice for scheduled match */}
            {isAdmin && match.status==="scheduled" && preds.length>0 && !isOpen && (
              <div style={{margin:"0 18px 14px",background:"#fff7ed",border:"1px solid #fed7aa",borderRadius:"8px",padding:"8px 14px",display:"flex",alignItems:"center",gap:"8px"}}>
                <span>🛡️</span>
                <span style={{fontSize:"12px",color:"#9a3412",fontWeight:700}}>
                  {preds.length} prediction{preds.length!==1?"s":""} submitted — hidden from users until kickoff
                </span>
                <button onClick={()=>setExpandedMatch(match.id)}
                  style={{marginLeft:"auto",padding:"3px 10px",borderRadius:"6px",fontSize:"11px",fontWeight:700,cursor:"pointer",border:"1px solid #fed7aa",background:"#fff",color:"#9a3412"}}>
                  View
                </button>
              </div>
            )}

            {/* Predictions table */}
            {!hidden && isOpen && (
              <div style={{borderTop:"1px solid #f1f5f9",overflowX:"auto"}}>
                {preds.length===0 ? (
                  <div style={{padding:"24px",textAlign:"center"}}>
                    <div style={{fontSize:"28px",marginBottom:"8px"}}>📭</div>
                    <div style={{color:"#94a3b8",fontSize:"13px",fontWeight:700,marginBottom:"8px"}}>No predictions found for this match</div>
                    {isAdmin && <div style={{fontSize:"11px",color:"#cbd5e1",marginBottom:"10px"}}>Check browser console for API response details</div>}
                    <button onClick={fetchPredictions}
                      style={{padding:"6px 16px",borderRadius:"8px",border:"1px solid #e2e8f0",background:"#f8fafc",fontSize:"12px",fontWeight:700,cursor:"pointer",color:"#334155"}}>
                      ↻ Retry
                    </button>
                  </div>
                ) : (
                  <table style={{width:"100%",borderCollapse:"collapse"}}>
                    <thead>
                      <tr style={{background:"#f8fafc",borderBottom:"1px solid #e2e8f0"}}>
                        {["#","Player","Country","Predicted","Final Score","Outcome","Points"].map(h=>(
                          <th key={h} style={{padding:"9px 14px",fontSize:"10px",fontWeight:800,textTransform:"uppercase",letterSpacing:"0.08em",color:"#94a3b8",textAlign:"left",whiteSpace:"nowrap"}}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {preds.map((p,i)=>{
                        const mine = String(p.user_id)===String(currentUser.id) || p.user_name===currentUser.name;
                        const o    = p.scoring_reason||p.status||"pending";
                        const os   = OS[o]||OS.pending;
                        return (
                          <tr key={p.id||i} style={{background:mine?"#f0fdf4":i%2===0?"#fff":"#fafafa",borderBottom:"1px solid #f1f5f9"}}>
                            <td style={{padding:"10px 14px",fontSize:"12px",color:"#94a3b8",fontFamily:"monospace"}}>{i+1}</td>
                            <td style={{padding:"10px 14px"}}>
                              <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
                                <div style={{width:"30px",height:"30px",borderRadius:"50%",flexShrink:0,
                                  background:mine?"linear-gradient(135deg,#10b981,#059669)":"linear-gradient(135deg,#94a3b8,#64748b)",
                                  display:"flex",alignItems:"center",justifyContent:"center",color:"#fff",fontWeight:800,fontSize:"12px"}}>
                                  {(p.user_name||"?")[0].toUpperCase()}
                                </div>
                                <div style={{fontWeight:700,fontSize:"13px",display:"flex",alignItems:"center",gap:"5px"}}>
                                  {p.user_name||("User #"+p.user_id)}
                                  {mine&&<span style={{padding:"1px 7px",background:"#d1fae5",color:"#065f46",borderRadius:"10px",fontSize:"10px",fontWeight:800,border:"1px solid #6ee7b7"}}>You</span>}
                                </div>
                              </div>
                            </td>
                            <td style={{padding:"10px 14px",fontSize:"12px",color:"#64748b"}}>{p.user_country||"—"}</td>
                            <td style={{padding:"10px 14px"}}>
                              <span style={{fontWeight:900,fontSize:"22px",letterSpacing:"3px",color:"#2563eb"}}>
                                {p.predicted_home_score} – {p.predicted_away_score}
                              </span>
                            </td>
                            <td style={{padding:"10px 14px"}}>
                              {match.status==="completed"
                                ?<span style={{fontWeight:900,fontSize:"22px",letterSpacing:"3px"}}>{match.home_score} – {match.away_score}</span>
                                :<span style={{fontSize:"11px",color:"#94a3b8",fontWeight:600}}>{match.status==="live"||kicked?"⏱ In Progress":"Awaiting"}</span>}
                            </td>
                            <td style={{padding:"10px 14px"}}>
                              <span style={{padding:"3px 10px",borderRadius:"20px",fontSize:"11px",fontWeight:700,background:os.bg,color:os.color,border:"1px solid "+os.border,whiteSpace:"nowrap"}}>
                                {os.label}
                              </span>
                            </td>
                            <td style={{padding:"10px 14px"}}>
                              <span style={{fontWeight:900,fontSize:"20px",color:"#d97706"}}>
                                {o!=="pending"?"+"+( p.points_awarded||0):"—"}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            )}

            {/* Collapsed avatar preview */}
            {!hidden && !isOpen && preds.length>0 && (
              <div style={{padding:"0 18px 14px",display:"flex",gap:"4px",flexWrap:"wrap",alignItems:"center"}}>
                {preds.slice(0,8).map((p,i)=>{
                  const mine=String(p.user_id)===String(currentUser.id)||p.user_name===currentUser.name;
                  return (
                    <div key={i} title={(p.user_name||"User")+" → "+p.predicted_home_score+"-"+p.predicted_away_score}
                      style={{width:"30px",height:"30px",borderRadius:"50%",
                        background:mine?"linear-gradient(135deg,#10b981,#059669)":"linear-gradient(135deg,#94a3b8,#64748b)",
                        display:"flex",alignItems:"center",justifyContent:"center",
                        color:"#fff",fontWeight:800,fontSize:"12px",
                        border:"2px solid #fff",cursor:"pointer",boxShadow:"0 1px 3px rgba(0,0,0,.15)"}}>
                      {(p.user_name||"?")[0].toUpperCase()}
                    </div>
                  );
                })}
                {preds.length>8&&<span style={{fontSize:"11px",color:"#94a3b8",fontWeight:700,marginLeft:"4px"}}>+{preds.length-8} more</span>}
                <button onClick={()=>setExpandedMatch(match.id)}
                  style={{marginLeft:"8px",fontSize:"11px",fontWeight:700,color:"#3b82f6",background:"none",border:"none",cursor:"pointer",padding:0}}>
                  View all →
                </button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  ADMIN MATCH MANAGER
// ─────────────────────────────────────────────────────────────────────────────
function AdminMatchManager({ matches, onAction, onDelete }) {
  const [filter,    setFilter]    = useState("all");
  const [search,    setSearch]    = useState("");
  const [confirmId, setConfirmId] = useState(null);

  const statusOrder = { live:0, locked:1, scheduled:2, completed:3 };

  let filtered = [...matches];
  if (filter !== "all") filtered = filtered.filter(m => effectiveMatchStatus(m) === filter);
  if (search.trim()) {
    const q = search.toLowerCase();
    filtered = filtered.filter(m =>
      (m.home_team||"").toLowerCase().includes(q) ||
      (m.away_team||"").toLowerCase().includes(q) ||
      (m.game_no||"").toLowerCase().includes(q) ||
      (m.stadium||"").toLowerCase().includes(q) ||
      (m.round||"").toLowerCase().includes(q)
    );
  }
  filtered.sort((a,b) => (statusOrder[effectiveMatchStatus(a)]??9)-(statusOrder[effectiveMatchStatus(b)]??9) || new Date(a.match_date)-new Date(b.match_date));

  // Count per status
  const counts = { all: matches.length };
  matches.forEach(m => {
    const s = effectiveMatchStatus(m);
    counts[s] = (counts[s]||0) + 1;
  });

  const confirmMatch = confirmId ? matches.find(m => m.id === confirmId) : null;

  return (
    <div style={{display:"flex",flexDirection:"column",gap:"14px"}}>

      {/* KPI row */}
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(110px,1fr))",gap:"10px"}}>
        {[
          {label:"All",       val:counts.all||0,       col:"#1d4ed8",bg:"#eff6ff",brd:"#bfdbfe",  key:"all"},
          {label:"Scheduled", val:counts.scheduled||0, col:"#7c3aed",bg:"#f5f3ff",brd:"#c4b5fd",  key:"scheduled"},
          {label:"Live",      val:counts.live||0,      col:"#b91c1c",bg:"#fef2f2",brd:"#fca5a5",  key:"live"},
          {label:"Locked",    val:counts.locked||0,    col:"#92400e",bg:"#fffbeb",brd:"#fde68a",  key:"locked"},
          {label:"Completed", val:counts.completed||0, col:"#059669",bg:"#f0fdf4",brd:"#6ee7b7",  key:"completed"},
        ].map(k=>(
          <div key={k.key} onClick={()=>setFilter(k.key)}
            style={{background:filter===k.key?k.col:k.bg, border:"1px solid "+(filter===k.key?k.col:k.brd),
              borderRadius:"10px", padding:"12px 14px", cursor:"pointer", transition:"all .15s"}}>
            <div style={{fontWeight:900,fontSize:"24px",color:filter===k.key?"#fff":k.col,lineHeight:1}}>{k.val}</div>
            <div style={{fontSize:"10px",fontWeight:700,color:filter===k.key?"rgba(255,255,255,.8)":k.col,
              textTransform:"uppercase",letterSpacing:"0.07em",marginTop:"3px"}}>{k.label}</div>
          </div>
        ))}
      </div>

      {/* Search */}
      <div style={{background:"#fff",border:"1px solid #e2e8f0",borderRadius:"12px",padding:"12px 16px",
        display:"flex",gap:"10px",alignItems:"center",flexWrap:"wrap"}}>
        <input
          style={{padding:"8px 12px",border:"1.5px solid #e2e8f0",borderRadius:"8px",fontSize:"13px",
            outline:"none",flex:1,minWidth:"180px"}}
          placeholder="🔍 Search teams, game no, venue, round…"
          value={search} onChange={e=>setSearch(e.target.value)}
        />
        <span style={{fontSize:"12px",fontWeight:700,color:"#94a3b8"}}>{filtered.length} match{filtered.length!==1?"es":""}</span>
      </div>

      {/* Delete confirm */}
      {confirmMatch && (
        <div style={{background:"#fee2e2",border:"1px solid #fca5a5",borderRadius:"10px",padding:"14px 18px",
          display:"flex",alignItems:"center",gap:"12px",flexWrap:"wrap"}}>
          <span style={{fontSize:"20px"}}>⚠️</span>
          <div style={{flex:1}}>
            <div style={{fontWeight:800,fontSize:"13px",color:"#991b1b"}}>
              Delete: {flagFor(confirmMatch.home_team)} {confirmMatch.home_team} vs {flagFor(confirmMatch.away_team)} {confirmMatch.away_team}
              {confirmMatch.game_no ? ` (${confirmMatch.game_no})` : ""}?
            </div>
            <div style={{fontSize:"11px",color:"#b91c1c",marginTop:"3px"}}>
              This permanently deletes the match and ALL its predictions. Cannot be undone.
            </div>
          </div>
          <button onClick={()=>{onDelete(confirmMatch.id);setConfirmId(null);}}
            style={{padding:"8px 18px",borderRadius:"8px",background:"#dc2626",color:"#fff",
              border:"none",fontWeight:800,fontSize:"13px",cursor:"pointer"}}>
            ✅ Yes, Delete
          </button>
          <button onClick={()=>setConfirmId(null)}
            style={{padding:"8px 18px",borderRadius:"8px",background:"#fff",color:"#64748b",
              border:"1px solid #e2e8f0",fontWeight:800,fontSize:"13px",cursor:"pointer"}}>
            Cancel
          </button>
        </div>
      )}

      {/* Match table */}
      <div style={{background:"#fff",border:"1px solid #e2e8f0",borderRadius:"14px",overflow:"hidden",
        boxShadow:"0 1px 4px rgba(0,0,0,.05)"}}>
        {filtered.length === 0 ? (
          <div style={{padding:"48px",textAlign:"center",color:"#94a3b8",fontWeight:700}}>
            No matches found.
          </div>
        ) : (
          <div style={{overflowX:"auto"}}>
            <table style={{width:"100%",borderCollapse:"collapse"}}>
              <thead>
                <tr style={{background:"#f8fafc",borderBottom:"1px solid #e2e8f0"}}>
                  {["Game","Teams","Round","Kickoff (NPT)","Venue","Status","Score","Actions"].map(h=>(
                    <th key={h} style={{padding:"10px 12px",fontSize:"10px",fontWeight:800,
                      textTransform:"uppercase",letterSpacing:"0.08em",color:"#94a3b8",
                      textAlign:"left",whiteSpace:"nowrap"}}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map((m,i)=>{
                  const eff = effectiveMatchStatus(m);
                  const leftColor = eff==="live"?"#ef4444":eff==="completed"?"#10b981":eff==="locked"?"#f59e0b":"#93c5fd";
                  const isCompleted = m.status === "completed";
                  return (
                    <tr key={m.id} style={{
                      borderBottom:"1px solid #f1f5f9",
                      background:i%2===0?"#fff":"#fafafa",
                      borderLeft:"3px solid "+leftColor,
                    }}>
                      <td style={{padding:"10px 12px",fontWeight:700,fontSize:"12px",color:"#64748b",whiteSpace:"nowrap"}}>
                        {m.game_no||"#"+m.id}
                      </td>
                      <td style={{padding:"10px 12px"}}>
                        <div style={{fontWeight:800,fontSize:"13px",whiteSpace:"nowrap"}}>
                          {flagFor(m.home_team,m.home_flag)} {shortName(m.home_team)}
                          <span style={{color:"#cbd5e1",margin:"0 6px",fontWeight:400}}>vs</span>
                          {flagFor(m.away_team,m.away_flag)} {shortName(m.away_team)}
                        </div>
                      </td>
                      <td style={{padding:"10px 12px",fontSize:"11px",color:"#64748b",whiteSpace:"nowrap"}}>
                        {m.round||"—"}
                      </td>
                      <td style={{padding:"10px 12px",fontSize:"11px",color:"#64748b",whiteSpace:"nowrap"}}>
                        {nepaliTime(m.match_date)}
                      </td>
                      <td style={{padding:"10px 12px",fontSize:"11px",color:"#94a3b8",maxWidth:"130px",
                        overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                        {m.stadium||"—"}
                      </td>
                      <td style={{padding:"10px 12px",whiteSpace:"nowrap"}}>
                        <StatusPill status={eff}/>
                      </td>
                      <td style={{padding:"10px 12px",textAlign:"center",whiteSpace:"nowrap"}}>
                        {isCompleted
                          ? <span style={{fontWeight:900,fontSize:"17px",letterSpacing:"2px",color:"#0f172a"}}>{m.home_score}–{m.away_score}</span>
                          : <span style={{color:"#cbd5e1",fontSize:"11px"}}>—</span>}
                      </td>
                      <td style={{padding:"10px 12px"}}>
                        <div style={{display:"flex",gap:"5px",flexWrap:"wrap"}}>
                          {/* Result button */}
                          <button onClick={()=>onAction("score",m)}
                            style={{padding:"4px 10px",borderRadius:"6px",fontSize:"11px",fontWeight:700,
                              cursor:"pointer",whiteSpace:"nowrap",
                              border:"1px solid #fde68a",background:"#fef9c3",color:"#92400e"}}>
                            📊 {isCompleted?"Update":"Enter Result"}
                          </button>

                          {/* Open/Close predictions — only for non-completed */}
                          {!isCompleted && (
                            <button onClick={()=>onAction("togglePred",m,!m.predictions_open)}
                              style={{padding:"4px 10px",borderRadius:"6px",fontSize:"11px",fontWeight:700,
                                cursor:"pointer",whiteSpace:"nowrap",
                                border:"1px solid "+(m.predictions_open?"#86efac":"#bfdbfe"),
                                background:m.predictions_open?"#dcfce7":"#dbeafe",
                                color:m.predictions_open?"#065f46":"#1d4ed8"}}>
                              {m.predictions_open?"🔒 Close Preds":"🔓 Open Preds"}
                            </button>
                          )}

                          {/* Delete — only completed matches */}
                          {isCompleted && (
                            <button onClick={()=>setConfirmId(m.id)}
                              style={{padding:"4px 10px",borderRadius:"6px",fontSize:"11px",fontWeight:700,
                                cursor:"pointer",whiteSpace:"nowrap",
                                border:"1px solid #fca5a5",background:"#fee2e2",color:"#dc2626"}}>
                              🗑️ Delete
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function Dashboard({ user, setUser }) {
  const [tab,          setTab]          = useState("games");
  const [tournaments,  setTournaments]  = useState([]);
  const [selected,     setSelected]     = useState(null);
  const [matches,      setMatches]      = useState([]);
  const [teams,        setTeams]        = useState([]);
  const [myPredictions,setMyPredictions]= useState([]);
  const [leaderboard,  setLeaderboard]  = useState([]);
  const [users,        setUsers]        = useState([]);
  const [analytics,    setAnalytics]    = useState(null);
  const [toast,        setToast]        = useState({ message: "", type: "info" });
  const [resultModal,  setResultModal]  = useState({ open: false, match: null });

  const msg = useCallback((m, t = "info") => setToast({ message: m, type: t }), []);

  async function load() {
    const list = await api.request("/tournaments");
    setTournaments(list);
    const chosen = selected || list[0]?.id;
    setSelected(chosen);
    if (chosen) {
      const [matchData, teamData, mine, leaders] = await Promise.all([
        api.request(`/matches?tournament_id=${chosen}`),
        api.request(`/tournaments/${chosen}/teams`),
        api.request("/predictions/mine"),
        api.request("/predictions/leaderboard"),
      ]);
      setMatches(matchData);
      setTeams(teamData);
      setMyPredictions(mine);
      setLeaderboard(leaders);
    }
    if (user.role === "admin") {
      const [stats, adminUsers] = await Promise.all([
        api.request("/admin/analytics"),
        api.request("/admin/users"),
      ]);
      setAnalytics(stats);
      setUsers(adminUsers);
    }
  }

  useEffect(() => { load().catch(err => msg(err.message, "error")); }, [selected]);

  // Unified action handler for MatchCard buttons
  async function handleAction(action, match, value) {
    try {
      if (action === "score") {
        setResultModal({ open: true, match });
      } else if (action === "ai") {
        const r = await api.request(`/matches/${match.id}/predict`);
        msg(`🤖 AI odds: ${match.home_team} ${r.home_probability}% / Draw ${r.draw_probability}% / ${match.away_team} ${r.away_probability}%`);
        await load();
      } else if (action === "togglePred") {
        await api.request(`/matches/${match.id}/prediction-status`, { method: "PUT", body: JSON.stringify({ predictions_open: value }) });
        msg(`Prediction ${value ? "opened ✅" : "closed 🔒"} for ${match.home_team} vs ${match.away_team}`);
        await load();
      }
    } catch (err) { msg("⚠️ " + err.message, "error"); }
  }

  async function handleDeleteMatch(matchId) {
    try {
      await api.request(`/matches/${matchId}`, { method: "DELETE" });
      msg("🗑️ Match deleted successfully.", "success");
      await load();
    } catch (err) { msg("⚠️ " + err.message, "error"); }
  }

  async function createTournament() {
    const name = prompt("Tournament name (e.g. FIFA World Cup 2026):", "FIFA World Cup 2026");
    if (!name) return;
    try {
      const t = await api.request("/tournaments", {
        method: "POST",
        body: JSON.stringify({
          name,
          sport: "FIFA World Cup",
          country: "Global",
          start_date: "2026-06-11T00:00:00Z",
          end_date: "2026-07-19T23:59:59Z",
        }),
      });
      msg(`✅ Tournament "${t.name}" created.`, "success");
      setSelected(t.id);
      await load();
    } catch (err) { msg("⚠️ " + err.message, "error"); }
  }

  async function importFixtures() {
    if (!selected) {
      msg("⚠️ Please select or create a tournament first.", "error");
      return;
    }
    try {
      const r = await api.request(`/admin/import-worldcup-fixtures/${selected}`, { method: "POST" });
      msg(`✅ Imported ${r.imported} fixtures. Skipped ${r.skipped}. Source: ${r.source}`);
      await load();
    } catch (err) { msg("⚠️ " + err.message, "error"); }
  }

  async function removeDuplicates() {
    if (!selected) {
      msg("⚠️ Please select a tournament first.", "error");
      return;
    }
    try {
      const preview = await api.request(`/admin/matches/${selected}/duplicates`);
      if (!preview.duplicate_groups) {
        msg("✅ No duplicate matches found — your schedule is already clean.", "success");
        return;
      }
      const confirmMsg = `Found ${preview.duplicate_groups} duplicate fixture(s), ${preview.extra_matches_to_remove} extra match row(s) to remove. Continue?`;
      if (!window.confirm(confirmMsg)) return;

      const r = await api.request(`/admin/matches/${selected}/duplicates`, { method: "DELETE" });
      msg(`🧹 ${r.message}`, "success");
      await load();
    } catch (err) { msg("⚠️ " + err.message, "error"); }
  }

  async function uploadSchedule(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    const data = new FormData();
    data.append("file", file);
    const resp = await fetch(`/api/admin/upload-schedule/${selected}`, {
      method: "POST",
      headers: api.token ? { Authorization: `Bearer ${api.token}` } : {},
      body: data,
    });
    if (!resp.ok) { const err = await resp.json(); msg(err.detail || "Upload failed", "error"); return; }
    const r = await resp.json();
    msg(`✅ Imported ${r.imported}, skipped ${r.skipped}. ${r.errors?.join(" ") || ""}`);
    e.target.value = "";
    await load();
  }

  function exportLeaderboard() {
    const rows = leaderboard.map((l, i) => ({
      Rank: i + 1, Name: l.name, Email: l.email || "", Country: l.country || "",
      Points: l.points, "Accuracy %": l.accuracy || 0, Badges: l.badges || "",
    }));
    downloadXLSX("WC2026_Leaderboard", [{ name: "Leaderboard", rows }]);
    msg("✅ Leaderboard exported to Excel.", "success");
  }

  function exportAllPredictions(allPreds, matchMap) {
    const rows = allPreds.map(p => {
      const m = matchMap[p.match_id] || {};
      const o = p.scoring_reason || p.status || "pending";
      return {
        User:           p.user_name || p.user_id,
        Match:          (m.home_team || "") + " vs " + (m.away_team || ""),
        Round:          m.round || "",
        "Date (NPT)":   nepaliTime(m.match_date),
        Prediction:     p.predicted_home_score + "-" + p.predicted_away_score,
        "Final Score":  m.status === "completed" ? m.home_score + "-" + m.away_score : "Pending",
        Outcome:        o.replace(/_/g, " "),
        Points:         p.points_awarded || 0,
        "Submitted At": p.created_at ? new Date(p.created_at).toLocaleString() : "",
      };
    });
    downloadXLSX("WC2026_All_Predictions", [{ name: "All Predictions", rows }]);
    msg("✅ All predictions exported.", "success");
  }

  const isAdmin = user.role === "admin";
  const totals  = analytics?.totals || {
    users: users.length,
    matches: matches.length,
    predictions: myPredictions.length,
    completed_matches: matches.filter(m => m.status === "completed").length,
  };
  const me = leaderboard.find(l => l.id === user.id) || {};

  const tabs = [
    { id: "games",         label: "⚽ Games" },
    { id: "mypreds",       label: "🎯 My Predictions" },
    { id: "predlist",      label: "👥 Prediction List" },
    { id: "leaderboard",   label: "🏆 Leaderboard" },
    ...(isAdmin ? [
      { id: "admin_matches",  label: "🗂️ Manage Matches" },
      { id: "admin_autoresult",label: "📡 Auto Result Post" },
      { id: "admin_users",    label: "👥 Users" },
      { id: "admin_reports",  label: "📊 Reports & Settings" },
    ] : []),
  ];

  return (
    <main className="min-h-screen bg-slate-50">
      {/* ── Header ── */}
      <header className="wc-bg text-white relative overflow-hidden">
        {/* Subtle decorative glow accents */}
        <div style={{position:"absolute",top:"-60px",right:"-60px",width:"260px",height:"260px",borderRadius:"50%",background:"radial-gradient(circle,rgba(255,255,255,.18),transparent 70%)",pointerEvents:"none"}} />
        <div style={{position:"absolute",bottom:"-80px",left:"10%",width:"200px",height:"200px",borderRadius:"50%",background:"radial-gradient(circle,rgba(16,185,129,.25),transparent 70%)",pointerEvents:"none"}} />

        <div className="max-w-7xl mx-auto px-4 py-6 relative">
          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
            <div>
              <div style={{display:"inline-flex",alignItems:"center",gap:"6px",background:"rgba(255,255,255,.12)",border:"1px solid rgba(255,255,255,.25)",borderRadius:"20px",padding:"4px 14px",backdropFilter:"blur(4px)"}}>
                <span style={{fontSize:"13px"}}>🏆</span>
                <span className="text-xs font-black uppercase tracking-widest">WorldCup 2026</span>
              </div>
              <h1 className="text-3xl md:text-5xl font-black mt-3 leading-tight" style={{textShadow:"0 2px 12px rgba(0,0,0,.25)"}}>
                Score Prediction <span style={{background:"linear-gradient(90deg,#fde68a,#fff)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent",backgroundClip:"text"}}>Platform</span>
              </h1>
              <p className="text-white/75 text-sm mt-2 flex flex-wrap gap-x-2 gap-y-1 items-center">
                <span>⚽ Exact-score predictions</span><span className="opacity-40">·</span>
                <span>🔒 Locked picks</span><span className="opacity-40">·</span>
                <span>📡 Live results</span><span className="opacity-40">·</span>
                <span>🏅 Rankings</span>
              </p>
            </div>

            <div style={{background:"rgba(255,255,255,.97)",borderRadius:"16px",padding:"14px 18px",boxShadow:"0 8px 30px rgba(0,0,0,.25)",border:"1px solid rgba(255,255,255,.4)"}}
              className="text-slate-900 flex items-center gap-3 self-start">
              <div style={{width:"44px",height:"44px",borderRadius:"50%",background:"linear-gradient(135deg,#10b981,#047857)",boxShadow:"0 3px 10px rgba(16,185,129,.4)"}}
                className="flex items-center justify-center font-black text-white text-lg flex-shrink-0">
                {user.name?.[0]?.toUpperCase()}
              </div>
              <div>
                <div className="font-black text-sm">{user.name}</div>
                <div className="text-xs text-slate-500">{user.role} · {user.country}</div>
                {!isAdmin && me.rank && (
                  <div style={{display:"inline-flex",alignItems:"center",gap:"4px",background:"#fef9c3",border:"1px solid #fde047",borderRadius:"10px",padding:"1px 8px",marginTop:"3px"}}>
                    <span style={{fontSize:"10px"}}>🥇</span>
                    <span className="text-xs font-black text-amber-800">Rank #{me.rank} · {me.points || 0} pts</span>
                  </div>
                )}
              </div>
              <button
                className="ml-2 text-xs font-black text-red-700 bg-red-50 hover:bg-red-100 border border-red-200 rounded-lg px-3 py-1.5 transition-colors"
                onClick={() => { localStorage.removeItem("wc_token"); setUser(null); }}>
                Sign out
              </button>
            </div>
          </div>

          {/* Stat bar */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-6">
            {[
              { icon:"🏅", val: isAdmin ? totals.users : (me.rank ? `#${me.rank}` : "—"), label: isAdmin ? "Total Users" : "My Rank" },
              { icon:"⚽", val: totals.matches, label: "Matches" },
              { icon:"⭐", val: isAdmin ? totals.predictions : (me.points || 0), label: isAdmin ? "Predictions" : "My Points" },
              { icon:"🎯", val: isAdmin ? totals.completed_matches : `${me.accuracy || 0}%`, label: isAdmin ? "Completed" : "Accuracy" },
            ].map((s, i) => (
              <div key={i} style={{
                background:"rgba(255,255,255,.13)",border:"1px solid rgba(255,255,255,.22)",
                borderRadius:"14px",padding:"14px 16px",backdropFilter:"blur(6px)",
                transition:"transform .15s",
              }}>
                <div className="flex items-center gap-2">
                  <span style={{fontSize:"18px",opacity:.85}}>{s.icon}</span>
                  <div className="text-2xl font-black">{s.val}</div>
                </div>
                <div className="text-xs opacity-70 uppercase tracking-wide mt-1.5 font-semibold">{s.label}</div>
              </div>
            ))}
          </div>
        </div>
      </header>

      <div className="mt-6 text-center text-xs text-slate-400 tracking-wide">~~~~~developed by/abs@techgen~~~~~</div>

      {/* ── Tournament bar (sticky) ── */}
      <div className="bg-white border-b shadow-sm sticky top-0 z-30">
        <div className="max-w-7xl mx-auto px-4 py-2.5 flex items-center gap-3 flex-wrap">
          <select className="input max-w-xs text-sm py-2" value={selected || ""} onChange={e => setSelected(Number(e.target.value))}>
            {!tournaments.length && <option value="">No tournaments yet — create one →</option>}
            {tournaments.map(t => <option key={t.id} value={t.id}>{t.name} – {t.sport}</option>)}
          </select>
          {isAdmin && (
            <button onClick={createTournament} className="btn btn-secondary text-xs py-2 px-3">➕ New Tournament</button>
          )}
          {isAdmin && (
            <div className="flex gap-2 flex-wrap ml-auto">
              <button onClick={importFixtures} className="btn btn-primary text-xs py-2 px-3">⬇️ Import FIFA Fixtures</button>
              <button onClick={removeDuplicates} className="btn btn-secondary text-xs py-2 px-3">🧹 Remove Duplicate Matches</button>
              <label className="btn btn-secondary text-xs py-2 px-3 cursor-pointer">
                📤 Upload Excel Schedule
                <input className="hidden" type="file" accept=".xlsx,.xls" onChange={uploadSchedule} />
              </label>
            </div>
          )}
        </div>
        {isAdmin && (
          <div className="max-w-7xl mx-auto px-4 pb-2 text-xs text-slate-400">
            Excel columns: <strong>game_no</strong>, <strong>team_a</strong>, <strong>team_b</strong>, <strong>venue</strong>, <strong>match_date</strong>. Optional: <strong>sport</strong>, <strong>round</strong>.
          </div>
        )}
      </div>

      {/* ── Nav tabs ── */}
      <div className="bg-white border-b">
        <div className="max-w-7xl mx-auto px-4 flex gap-0 overflow-x-auto">
          {tabs.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`py-3 px-4 text-sm font-black whitespace-nowrap border-b-2 transition-colors ${tab === t.id ? "border-emerald-500 text-emerald-700" : "border-transparent text-slate-500 hover:text-slate-800"}`}>
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── Page content ── */}
      <div className="max-w-7xl mx-auto px-4 py-6">
        {tab === "games" && (
          <GamesPage
            matches={matches}
            user={user}
            myPredictions={myPredictions}
            onAction={handleAction}
            onPredSaved={async m => { msg(m, "success"); await load(); }}
          />
        )}

        {tab === "mypreds" && (
          <MyPredictionsPage predictions={myPredictions} matches={matches} user={user} />
        )}

        {tab === "predlist" && (
          <PredictionListPage matches={matches} currentUser={user} myPredictions={myPredictions} leaderboard={leaderboard} />
        )}

        {tab === "leaderboard" && (
          <LeaderboardPage leaderboard={leaderboard} currentUser={user} onExport={exportLeaderboard} />
        )}

        {tab === "admin_matches" && isAdmin && (
          <div style={{display:"flex",flexDirection:"column",gap:"20px"}}>
            {/* Create match form */}
            <AdminMatchForm
              selected={selected}
              teams={teams}
              onSaved={async m => { msg(m, "success"); await load(); }}
            />
            {/* Match management table with delete */}
            <AdminMatchManager
              matches={matches}
              onAction={handleAction}
              onDelete={handleDeleteMatch}
            />
          </div>
        )}

        {tab === "admin_autoresult" && isAdmin && (
          <AutoResultPostPanel selected={selected} onRefresh={async m => { if (m) msg(m, "success"); await load(); }} />
        )}

        {tab === "admin_users" && isAdmin && (
          <AdminUsersPage users={users} onRefresh={load} onMessage={m => msg(m)} currentUser={user} />
        )}

        {tab === "admin_reports" && isAdmin && (
          <div className="space-y-6">
            <AdminAllPredictions matches={matches} onExport={exportAllPredictions} />
            <AdminReportsPanel selected={selected} users={users} onMessage={m => msg(m, "success")} />
          </div>
        )}
      </div>

      {/* ── Result modal ── */}
      <ResultModal
        match={resultModal.match}
        open={resultModal.open}
        onClose={() => setResultModal(s => ({ ...s, open: false }))}
        onSaved={async m => { msg(m, "success"); await load(); }}
      />

      {/* ── Toast ── */}
      <Toast message={toast.message} type={toast.type} onClose={() => setToast(t => ({ ...t, message: "" }))} />
    </main>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  ROOT
// ─────────────────────────────────────────────────────────────────────────────
function App() {
  const [user, setUser] = useState(null);
  return user ? <Dashboard user={user} setUser={setUser} /> : <Login onLogin={setUser} />;
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
