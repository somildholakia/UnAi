import { useEffect, useMemo, useState } from 'react';

type Message = { role: 'user' | 'assistant'; content: string };
type Health = { status: string; provider_configured: boolean; model: string; execution: string };

const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

const suggestions = [
  { icon: '⌘', title: 'Understand a codebase', description: 'Map architecture, dependencies and risky areas.', prompt: 'Analyze this codebase architecture and identify the most important modules, dependencies, and risks.' },
  { icon: '⌕', title: 'Find a difficult bug', description: 'Build hypotheses and tests that can disprove them.', prompt: 'Help me investigate a difficult bug. Start by listing falsifiable hypotheses and the evidence needed to distinguish them.' },
  { icon: '◇', title: 'Design a system', description: 'Turn requirements into a buildable technical plan.', prompt: 'Help me design a production-grade system. Ask for missing constraints, then produce an implementation plan and verification strategy.' },
];

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState('');

  const online = health?.status === 'ok';
  const modelLabel = useMemo(() => health?.model || 'Local engine not connected', [health]);

  useEffect(() => {
    let active = true;
    const check = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/health`);
        if (!response.ok) throw new Error('Engine unavailable');
        const data = (await response.json()) as Health;
        if (active) setHealth(data);
      } catch {
        if (active) setHealth(null);
      }
    };
    void check();
    const interval = window.setInterval(check, 15000);
    return () => { active = false; window.clearInterval(interval); };
  }, []);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    const next = [...messages, { role: 'user' as const, content: trimmed }];
    setMessages(next);
    setInput('');
    setBusy(true);
    setError('');
    try {
      const response = await fetch(`${API_BASE}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: trimmed, history: messages.slice(-20) }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
      setMessages([...next, { role: 'assistant', content: data.message }]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to reach the engine.');
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void send(input);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void send(input);
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">u<span>.</span></span><span className="brand-name">unAI</span></div>
        <button className="new-task" onClick={() => { setMessages([]); setError(''); }}><span>＋</span> New task <kbd>⌘ K</kbd></button>
        <div className="side-label">WORKSPACE</div>
        <div className="side-item active"><span className="side-glyph">✳</span> Intelligence lab</div>
        <div className="side-item muted"><span className="side-glyph">⌕</span> Repository map <span className="soon">SOON</span></div>
        <div className="side-item muted"><span className="side-glyph">◇</span> Experiments <span className="soon">SOON</span></div>
        <div className="sidebar-bottom">
          <div className="side-label">RUNTIME</div>
          <div className="runtime-card">
            <div className="runtime-head"><span className={`status-dot ${online ? 'online' : ''}`} />{online ? 'Engine online' : 'Engine offline'}</div>
            <div className="runtime-model">{modelLabel}</div>
            <div className="runtime-foot">{health?.execution || 'Waiting for local runtime'}</div>
          </div>
          <a className="github-link" href="https://github.com/somildholakia/UnAi" target="_blank" rel="noreferrer">↗ Project repository</a>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div className="crumb">Workspace <span>/</span> <b>Intelligence lab</b></div>
          <div className="topbar-right"><span className="mode-pill">LOCAL-FIRST</span><div className="avatar">S</div></div>
        </header>

        <section className={`content ${messages.length ? 'has-messages' : ''}`}>
          {messages.length === 0 ? (
            <div className="welcome">
              <div className="eyebrow"><span className="eyebrow-line" /> AN EXPERIMENT IN BETTER INTELLIGENCE</div>
              <h1>Don't just generate.<br /><em>Prove it works.</em></h1>
              <p className="intro">An execution-first system for the work that breaks ordinary workflows — complex code, deep debugging, and hard technical problems.</p>
              <div className="principles"><span>01&nbsp; STRUCTURE THE PROBLEM</span><i /> <span>02&nbsp; EXPLORE SOLUTIONS</span><i /> <span>03&nbsp; VERIFY OUTCOMES</span></div>
              <div className="suggestion-grid">
                {suggestions.map((item) => (
                  <button className="suggestion" key={item.title} onClick={() => setInput(item.prompt)}>
                    <span className="suggestion-icon">{item.icon}</span><strong>{item.title}</strong><small>{item.description}</small><span className="suggestion-arrow">↗</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="conversation">
              <div className="conversation-heading"><span>ACTIVE SESSION</span><span>{messages.filter(m => m.role === 'user').length} OBJECTIVE(S)</span></div>
              {messages.map((message, index) => (
                <article className={`message ${message.role}`} key={`${index}-${message.role}`}>
                  <div className="message-avatar">{message.role === 'user' ? 'S' : 'u.'}</div>
                  <div className="message-body"><div className="message-role">{message.role === 'user' ? 'YOU' : 'UNAI'}</div><div className="message-content">{message.content}</div></div>
                </article>
              ))}
              {busy && <div className="thinking"><span className="pulse" /> Reasoning with local model…</div>}
            </div>
          )}
        </section>

        <div className="composer-wrap">
          {!online && <div className="connection-note"><span className="status-dot" /> Connect the local engine to start a session. No hosted API key is required in local mode.</div>}
          {error && <div className="error-note">{error}</div>}
          <form className="composer" onSubmit={onSubmit}>
            <textarea value={input} onChange={e => setInput(e.target.value)} onKeyDown={onKeyDown} placeholder="Describe the outcome. Include constraints, context, and what success looks like…" rows={2} disabled={busy} />
            <div className="composer-bottom"><div className="composer-hint"><span>↵</span> to send <span className="hint-divider">·</span> shift + ↵ for a new line</div><button className="send-button" type="submit" disabled={!input.trim() || busy}>{busy ? 'Working…' : 'Start task'} <span>↗</span></button></div>
          </form>
          <div className="footnote">unAI can make mistakes. Important changes must be reviewed and verified. <span>Built around evidence, not promises.</span></div>
        </div>
      </main>
    </div>
  );
}
