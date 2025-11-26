import React, { useEffect, useRef, useState } from "react";

type Message = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  ts: number;
};

type ChatSession = {
  chat_id: string;
  title: string;
  messages: Message[];
  summary?: string;
};

const STORAGE_KEY = "custom_chat_widget_sessions_v1";

const makeId = (prefix = "id") => `${prefix}_${Math.random().toString(36).slice(2, 9)}`;

const CustomChatWidget: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);

  // Load local sessions on mount
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

  // Persist sessions locally
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
  }, [sessions]);

  // Scroll to bottom when messages change
  useEffect(() => {
    if (!listRef.current) return;
    listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [sessions, activeSessionId, open, drawerOpen]);

  const activeSession = sessions.find((s) => s.chat_id === activeSessionId) || null;

  const createNewSession = (title?: string) => {
    const id = makeId("chat");
    const newSession: ChatSession = {
      chat_id: id,
      title: title || `Conversation ${sessions.length + 1}`,
      messages: [
        {
          id: makeId("sys"),
          role: "system",
          text: "Hello Doctor! I am your radiologist assistant.",
          ts: Date.now(),
        },
      ],
    };
    setSessions((s) => [newSession, ...s]);
    setActiveSessionId(id);
    setDrawerOpen(false);
    setOpen(true);
  };

  const deleteSession = (chat_id: string) => {
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
    if (!activeSessionId) createNewSession();

    const sessionId = activeSessionId ?? sessions[0]?.chat_id ?? null;
    if (!sessionId) return;

    const userMsg: Message = { id: makeId("u"), role: "user", text, ts: Date.now() };
    setSessions((prev) =>
      prev.map((s) => (s.chat_id === sessionId ? { ...s, messages: [...s.messages, userMsg] } : s))
    );

    setInput("");
    setIsTyping(true);

    const replyText = await mockAgentResponse(text);
    const assistantMsg: Message = { id: makeId("a"), role: "assistant", text: replyText, ts: Date.now() };

    setSessions((prev) =>
      prev.map((s) => (s.chat_id === sessionId ? { ...s, messages: [...s.messages, assistantMsg] } : s))
    );

    setIsTyping(false);
  };

  const openSession = (chat_id: string) => {
    setActiveSessionId(chat_id);
    setOpen(true);
    setDrawerOpen(false);
  };

  // Fetch chat sessions from backend
  useEffect(() => {
    const fetchChatSessions = async () => {
      try {
        const r = await fetch("http://localhost:8000/chat_sessions/");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        console.log("Fetched sessions from backend:", data); // <-- add this
        const mapped: ChatSession[] = (data || []).map((s: any) => ({
          chat_id: s.chat_id.toString(),
          title: s.title || `Chat ${s.chat_id}`,
          messages: s.messages || [],
          summary: s.summary,
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
    fetchChatSessions();
  }, []);

  return (
    <div className="fixed bottom-6 right-6 z-50">
      {/* Floating launcher */}
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

      {/* Widget panel */}
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

          {/* Main content: drawer + chat */}
          <div className="flex-1 flex min-h-0">
            {/* Drawer */}
            <div className={`transition-all duration-200 border-r ${drawerOpen ? "w-56" : "w-0 overflow-hidden"}`}>
              <div className="h-full flex flex-col">
                <div className="px-3 py-3 border-b flex items-center justify-between">
                  <div className="text-sm font-medium">History</div>
                  <button onClick={() => setDrawerOpen(false)} className="text-sm">✕</button>
                </div>
                <div className="p-3 overflow-auto flex-1">
                  {sessions.length === 0 ? (
                    <div className="text-xs text-gray-500">No conversations yet.</div>
                  ) : (
                    <ul className="space-y-2">
                      {sessions.map((s) => (
                        <li
                          key={s.chat_id}
                          className={`${s.chat_id === activeSessionId ? "bg-sky-50" : ""} p-2 rounded-md hover:bg-gray-50 cursor-pointer flex justify-between items-start`}
                          onClick={() => openSession(s.chat_id)}
                        >
                          <div>
                            <div className="text-sm font-medium truncate" style={{ maxWidth: 160 }}>{s.title}</div>
                            <div className="text-xs text-gray-500 truncate" style={{ maxWidth: 160 }}>
                              {s.summary || (s.messages[s.messages.length - 1]?.text.slice(0, 60) ?? "")}
                            </div>
                          </div>
                          <div className="flex flex-col items-end ml-2">
                            <button
                              title="Delete"
                              onClick={(e) => { e.stopPropagation(); deleteSession(s.chat_id); }}
                              className="text-xs text-red-500"
                            >
                              Del
                            </button>
                            <div className="text-xs text-gray-400 mt-2">
                              {new Date(s.messages[s.messages.length - 1]?.ts || s.messages[0]?.ts).toLocaleTimeString()}
                            </div>
                          </div>
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
                {activeSession ? (
                  <>
                    {activeSession.messages.map((m) => (
                      <div key={m.id} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                        <div className="overflow-x-auto max-w-full">
                          <div className={`${m.role === "user" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-900"} p-3 rounded-lg shadow break-words whitespace-pre-wrap inline-block`} style={{ overflowWrap: "anywhere", minWidth: "fit-content" }}>
                            {m.text}
                          </div>
                        </div>
                      </div>
                    ))}
                    {/* Render summary as last system message */}
                    {activeSession.summary && (
                      <div className="flex justify-start">
                        <div className="bg-gray-200 p-3 rounded-lg text-sm text-gray-700 shadow break-words whitespace-pre-wrap">
                          {activeSession.summary}
                        </div>
                      </div>
                    )}
                  </>
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

              {/* Input */}
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
