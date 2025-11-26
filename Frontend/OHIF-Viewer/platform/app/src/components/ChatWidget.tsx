import React, { useEffect, useRef, useState } from "react";

type Message = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  ts: number;
};

type ChatSession = {
  chat_id: number;
  title: string;
  messages: Message[];
  summary?: string;
  last_activity?: number; // store timestamp of last activity
};

const STORAGE_KEY = "custom_chat_widget_sessions_v1";

const makeId = (prefix = "id") => `${prefix}_${Math.random().toString(36).slice(2, 9)}`;

const CustomChatWidget: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null);
  const [input, setInput] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);

  // Load local sessions
  useEffect(() => {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      try {
        const parsed: ChatSession[] = JSON.parse(raw);
        setSessions(parsed);
        if (parsed.length > 0) setActiveSessionId(parsed[0].chat_id);
      } catch (e) {
        console.warn("Failed to parse local sessions", e);
      }
    }
  }, []);

  // Persist sessions
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
  }, [sessions]);

  // Scroll to bottom
  useEffect(() => {
    if (!listRef.current) return;
    listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [sessions, activeSessionId, open, drawerOpen]);

  const activeSession = sessions.find((s) => s.chat_id === activeSessionId) || null;

  // Messages with pinned summary at top
  const messagesWithSummary = activeSession
    ? activeSession.summary
      ? [
          {
            id: "summary",
            role: "assistant",
            text: activeSession.summary,
            ts: 0, // pinned at top
          },
          ...activeSession.messages,
        ]
      : activeSession.messages
    : [];

  // Backend integration
  const fetchChatSessions = async () => {
    try {
      const r = await fetch("http://localhost:8000/chat_sessions/");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      const mapped: ChatSession[] = (data || []).map((s: any) => ({
        chat_id: Number(s.chat_id),
        title: s.title || `Chat ${s.chat_id}`,
        messages: [],
        summary: s.summary,
        last_activity: s.last_activity ? new Date(s.last_activity).getTime() : 0,
      }));
      setSessions((prev) => {
        const existingIds = new Set(prev.map((p) => p.chat_id));
        const newSessions = mapped.filter((m) => !existingIds.has(m.chat_id));
        return [...prev, ...newSessions];
      });
    } catch (e) {
      console.warn("Failed to fetch sessions from backend", e);
    }
  };

  useEffect(() => {
    fetchChatSessions();
  }, []);

  const createNewSession = async (title?: string) => {
    try {
      const payload = { title: title || `chat ${sessions.length + 1}` };
      const r = await fetch("http://localhost:8000/chat_sessions/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      const newSession: ChatSession = {
        chat_id: Number(data.chat_id),
        title: data.title || payload.title,
        messages: [
          {
            id: makeId("sys"),
            role: "system",
            text: "Hello Doctor! I am your radiologist assistant.",
            ts: Date.now(),
          },
        ],
        summary: data.summary,
        last_activity: Date.now(),
      };
      setSessions((s) => [newSession, ...s]);
      setActiveSessionId(newSession.chat_id);
      setDrawerOpen(false);
      setOpen(true);
    } catch (err) {
      console.warn("Failed to create session", err);
    }
  };

  const updateSessionLastActivity = async (chat_id: number) => {
    try {
      await fetch(`http://localhost:8000/chat_sessions/${chat_id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ last_activity: new Date().toISOString() }),
      });
      // update locally too
      setSessions((prev) =>
        prev.map((s) =>
          s.chat_id === chat_id ? { ...s, last_activity: Date.now() } : s
        )
      );
    } catch (err) {
      console.warn("Failed to update last_activity", err);
    }
  };

  const deleteSession = async (chat_id: number) => {
    try {
      await fetch(`http://localhost:8000/chat_sessions/${chat_id}`, { method: "DELETE" });
    } catch (err) {
      console.warn("Failed to delete session", err);
    }
    setSessions((s) => s.filter((x) => x.chat_id !== chat_id));
    if (activeSessionId === chat_id) {
      const remaining = sessions.filter((x) => x.chat_id !== chat_id);
      setActiveSessionId(remaining.length ? remaining[0].chat_id : null);
    }
  };

  const mockAgentResponse = async (text: string) => {
    try {
      const res = await fetch("http://localhost:8150/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task: text }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      return data.response || "No response from agent.";
    } catch (err) {
      console.warn("Agent fetch failed", err);
      await new Promise((r) => setTimeout(r, 600));
      return "⚠️ Could not reach the radiologist assistant server. Offline reply.";
    }
  };

  const sendMessage = async (text: string) => {
    if (!text.trim()) return;

    if (!activeSessionId) await createNewSession();

    const sessionId = activeSessionId ?? sessions[0]?.chat_id ?? null;
    if (!sessionId) return;

    const userMsg: Message = { id: makeId("u"), role: "user", text, ts: Date.now() };
    setSessions((prev) =>
      prev.map((s) => (s.chat_id === sessionId ? { ...s, messages: [...s.messages, userMsg], last_activity: Date.now() } : s))
    );

    setInput("");
    setIsTyping(true);

    try {
      const replyText = await mockAgentResponse(text);
      const assistantMsg: Message = { id: makeId("a"), role: "assistant", text: replyText, ts: Date.now() };
      setSessions((prev) =>
        prev.map((s) => (s.chat_id === sessionId ? { ...s, messages: [...s.messages, assistantMsg], last_activity: Date.now() } : s))
      );
    } catch (err) {
      console.warn("Assistant reply failed", err);
    } finally {
      setIsTyping(false);
      await updateSessionLastActivity(sessionId);
    }
  };

  const openSession = async (chat_id: number) => {
    setActiveSessionId(chat_id);
    setOpen(true);
    setDrawerOpen(false);
    
  };

  // Sort sessions by last_activity descending
  const sortedSessions = [...sessions].sort((a, b) => (b.last_activity || 0) - (a.last_activity || 0));

  return (
    <div className="fixed bottom-6 right-6 z-50">
      {!open && (
        <button
          onClick={() => setOpen(true)}
          aria-label="Open chat"
          className="flex items-center justify-center w-14 h-14 rounded-full shadow-lg bg-white ring-1 ring-gray-200"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="w-7 h-7 text-sky-600" viewBox="0 0 24 24" fill="none" stroke="currentColor">
            <path strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" d="M21 12a9 9 0 1 0-3.95 7.06L21 21l-1.06-2.95A8.96 8.96 0 0 0 21 12z" />
          </svg>
        </button>
      )}

      {open && (
        <div className="w-full max-w-[800px] h-[560px] bg-white shadow-2xl rounded-xl relative flex flex-col overflow-hidden">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b">
            <div className="flex items-center gap-3">
              <img src="/assets/robot.png" alt="bot" className="w-10 h-10" />
              <div>
                <div className="font-semibold text-sm">AI Assistant</div>
                <div className="text-xs text-gray-500">Hello Doctor, how can I help you today?</div>
              </div>
            </div>
            <div className="flex items-center gap-1">
              <button title="History" onClick={() => setDrawerOpen((v) => !v)} className="px-2 py-1 rounded-md hover:bg-gray-100">📜</button>
              <button title="New" onClick={() => createNewSession()} className="px-2 py-1 rounded-md hover:bg-gray-100">➕</button>
              <button title="Close" onClick={() => setOpen(false)} className="ml-1 text-lg font-bold px-2 py-1 rounded-md hover:bg-gray-100">×</button>
            </div>
          </div>

          <div className="flex-1 flex min-h-0">
            {/* Drawer */}
            <div className={`transition-all duration-200 border-r ${drawerOpen ? "w-56" : "w-0 overflow-hidden"}`}>
              <div className="h-full flex flex-col">
                <div className="px-3 py-3 border-b flex items-center justify-between">
                  <div className="text-sm font-medium">History</div>
                  <button onClick={() => setDrawerOpen(false)} className="text-sm">✕</button>
                </div>
                <div className="p-3 overflow-auto flex-1">
                  {sortedSessions.length === 0 ? (
                    <div className="text-xs text-gray-500">No conversations yet.</div>
                  ) : (
                    <ul className="space-y-2">
                      {sortedSessions.map((s) => (
                        <li
                            key={s.chat_id}
                            className={`p-2 rounded-lg cursor-pointer flex justify-between items-start
                              ${s.chat_id === activeSessionId ? "bg-sky-500 shadow-md font-semibold" : "hover:bg-gray-50"}`}
                          >
                            <div onClick={() => openSession(s.chat_id)} className="flex-1">
                              <div className="text-sm font-medium truncate" style={{ maxWidth: 160 }}>{s.title}</div>
                              <div className="text-xs text-gray-500 truncate" style={{ maxWidth: 160 }}>
                                {s.summary ?? s.messages[s.messages.length - 1]?.text?.slice(0, 60) ?? ""}
                              </div>
                            </div>
                            <button
                              title="Delete"
                              onClick={(e) => { e.stopPropagation(); deleteSession(s.chat_id); }}
                              className="ml-2 text-xs text-red-500 hover:text-red-700"
                            >
                              🗑️
                            </button>
                          </li>

                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </div>

            {/* Chat messages */}
            <div className="flex-1 flex flex-col min-h-0">
              <div ref={listRef} className="flex-1 p-4 overflow-y-auto space-y-3 bg-[linear-gradient(180deg,#ffffff,#f8fafc)]">
                {messagesWithSummary.length > 0 ? (
                  messagesWithSummary.map((m) => (
                    <div key={m.id} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                      <div className="overflow-x-auto max-w-full">
                        <div className={`${m.role === "user" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-900"} p-3 rounded-lg shadow break-words whitespace-pre-wrap inline-block`} style={{ overflowWrap: "anywhere", minWidth: "fit-content" }}>
                          {m.text}
                        </div>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="text-center text-sm text-gray-500 mt-12">
                    No conversation selected. Start a new chat or open history.
                  </div>
                )}

                {isTyping && (
                  <div className="flex justify-start">
                    <div className="bg-gray-100 p-2 rounded-lg text-xs text-gray-600">Typing...</div>
                  </div>
                )}
              </div>

              <div className="px-3 py-3 border-t">
                <form onSubmit={(e) => { e.preventDefault(); sendMessage(input); }} className="flex items-center gap-2">
                  <input
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder="Write a message..."
                    className="flex-1 rounded-lg px-3 py-2 border focus:outline-none focus:ring-1 focus:ring-sky-300"
                  />
                  <button type="submit" className="p-2 rounded-lg bg-sky-600 text-white flex items-center justify-center">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#1d4ed8" className="w-6 h-6">
                      <path d="M2 21l21-9L2 3v7l15 2-15 2v7z" />
                    </svg>
                  </button>
                </form>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default CustomChatWidget;
