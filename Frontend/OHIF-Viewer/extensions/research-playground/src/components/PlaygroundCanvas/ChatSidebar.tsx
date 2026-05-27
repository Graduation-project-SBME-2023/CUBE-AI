import React, { useState, useRef, useEffect } from 'react';
import { MessageSquare, History, Plus, ChevronRight, Trash2, Send, Activity as ActivityIcon } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import websocketService from '../../services/websocket';
import { getBackendApiBaseUrl } from '../../utils/apiBase';

const makeId = (prefix = 'id') => `${prefix}_${Math.random().toString(36).slice(2, 9)}`;
const renderIcon = (Icon, size) => React.createElement(Icon, { size, 'aria-hidden': true });
const isUuid = (value: unknown) =>
  typeof value === 'string' &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);

const makeLocalSessionId = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

const ChatSidebar = ({
  isOpen,
  onClose,
  initialUserMessage,
  externalSessionId,
  sendInitialMessage = false,
  onPlanApprovalNeeded,
}) => {
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [input, setInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(420);
  const [isResizing, setIsResizing] = useState(false);
  const [maxSidebarWidth, setMaxSidebarWidth] = useState(() =>
    Math.max(340, Math.min(760, Math.floor(window.innerWidth * 0.55)))
  );
  const resizeStateRef = useRef({ startX: 0, startWidth: 420 });
  const messagesEndRef = useRef(null);
  const activeSessionIdRef = useRef(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const MIN_WIDTH = 340;
  const MAX_WIDTH = maxSidebarWidth;
  const activeSession = sessions.find(s => String(s.chat_id) === String(activeSessionId)) || null;
  let messages = activeSession ? activeSession.messages : [];
  // If there are no messages and an initialUserMessage is provided, show it as the first user message
  if (messages.length === 0 && initialUserMessage) {
    messages = [{ id: makeId('init'), role: 'user', text: initialUserMessage, ts: Date.now() }];
  }
  const lastMessage = messages.length > 0 ? messages[messages.length - 1] : null;
  const showThinking = isTyping || lastMessage?.role === 'user';

  useEffect(() => {
    console.log('[HypothesisFlow][ChatSidebar] Session binding effect', {
      externalSessionId,
      sendInitialMessage,
      hasInitialUserMessage: Boolean(initialUserMessage),
      initialUserMessageLength: initialUserMessage?.length || 0,
    });

    if (externalSessionId) {
      setActiveSessionId(externalSessionId);
      console.log('[HypothesisFlow][ChatSidebar] Active session set from route state', {
        externalSessionId,
      });
      // Ensure a local session entry exists for this external ID
      setSessions(prev => {
        const exists = prev.some(s => String(s.chat_id) === String(externalSessionId));
        if (!exists) {
          return [{
            chat_id: externalSessionId,
            title: 'Research Session',
            messages: initialUserMessage
              ? [{ id: makeId('init'), role: 'user', text: initialUserMessage, ts: Date.now() }]
              : [],
            summary: '',
            last_activity: Date.now(),
          }, ...prev];
        }
        return prev;
      });
      // Optionally refresh sessions list so the new one appears in history
      fetchChatSessions();
    }
    if (sendInitialMessage) {
      // if (websocketService.isConnected()) {
      //   websocketService.sendMessage({ type: 'user', payload: initialUserMessage });
      // } else {
      //   alert('WebSocket not connected. Message not sent to backend in real time.');
      // }
      console.log('[HypothesisFlow][ChatSidebar] Sending initial message to chat backend', {
        externalSessionId,
        preview: (initialUserMessage || '').slice(0, 120),
      });
      if (!isUuid(String(externalSessionId))) {
        return;
      }
      fetch(`${getBackendApiBaseUrl()}/chat_messages/${externalSessionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: 'user', text: initialUserMessage }),
      })
        .then(response => {
          console.log('[HypothesisFlow][ChatSidebar] Initial message POST result', {
            ok: response.ok,
            status: response.status,
            externalSessionId,
          });
        })
        .catch(error => {
          console.error('[HypothesisFlow][ChatSidebar] Failed to send initial message', {
            externalSessionId,
            error,
          });
        });
    }
  }, [externalSessionId]);

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  useEffect(() => {
    if (!isResizing) {
      return undefined;
    }

    const handleMouseMove = event => {
      const delta = resizeStateRef.current.startX - event.clientX;
      const next = Math.max(
        MIN_WIDTH,
        Math.min(MAX_WIDTH, resizeStateRef.current.startWidth + delta)
      );
      setSidebarWidth(next);
    };

    const handleMouseUp = () => {
      setIsResizing(false);
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);

    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isResizing]);

  useEffect(() => {
    const handleWindowResize = () => {
      const nextMax = Math.max(340, Math.min(760, Math.floor(window.innerWidth * 0.55)));
      setMaxSidebarWidth(nextMax);
      setSidebarWidth(prev => Math.min(prev, nextMax));
    };

    window.addEventListener('resize', handleWindowResize);
    return () => {
      window.removeEventListener('resize', handleWindowResize);
    };
  }, []);

  const handleResizeStart = event => {
    resizeStateRef.current = {
      startX: event.clientX,
      startWidth: sidebarWidth,
    };
    setIsResizing(true);
  };

  const fetchChatSessions = async () => {
    try {
      const r = await fetch(`${getBackendApiBaseUrl()}/chat_sessions/`);
      if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
      }
      const data = await r.json();
      const mapped = (data || []).map(s => ({
        chat_id: s.chat_id,
        title: s.title || `Chat ${s.chat_id}`,
        messages: [],
        summary: s.summary,
        last_activity: s.last_activity ? new Date(s.last_activity).getTime() : 0,
      }));
      // Merge DB sessions with existing local sessions to preserve WS-delivered messages
      if (!isMountedRef.current) return;
      setSessions(prev => {
        const merged = [...prev];
        for (const dbSession of mapped) {
          const idx = merged.findIndex(s => String(s.chat_id) === String(dbSession.chat_id));
          if (idx === -1) {
            merged.push(dbSession);
          } else {
            // Keep existing messages (from WS) if DB returned none
            merged[idx] = {
              ...dbSession,
              messages: merged[idx].messages.length > 0 ? merged[idx].messages : dbSession.messages,
            };
          }
        }
        return merged;
      });
    } catch (err) {}
  };
  const fetchMessagesForSession = async chat_id => {
    if (!isUuid(String(chat_id))) {
      return;
    }
    try {
      const res = await fetch(`${getBackendApiBaseUrl()}/chat_messages/${chat_id}`);
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const msgs = await res.json();
      const dbMessages = msgs.map(m => ({
        id: `db_${m.message_id}`,
        role: m.role,
        text: m.text,
        ts: new Date(m.timestamp).getTime(),
      }));
      if (!isMountedRef.current) return;
      setSessions(prev =>
        prev.map(s => {
          if (String(s.chat_id) !== String(chat_id)) return s;
          // Merge: use DB messages as base, append any local-only messages not in DB
          const dbIds = new Set(dbMessages.map(m => m.id));
          const localOnly = s.messages.filter(m => !dbIds.has(m.id) && !m.id.startsWith('db_'));
          return { ...s, messages: [...dbMessages, ...localOnly] };
        })
      );
    } catch (err) {}
  };
  const createNewSession = async () => {
    try {
      const payload = { title: `Chat ${sessions.length + 1}` };
      const r = await fetch(`${getBackendApiBaseUrl()}/chat_sessions/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
      }
      const data = await r.json();
      const newSession = {
        chat_id: data.chat_id,
        title: data.title || payload.title,
        messages: [
          {
            id: makeId('sys'),
            role: 'system',
            text: 'Hello! I am your research assistant.',
            ts: Date.now(),
          },
        ],
        summary: data.summary,
        last_activity: Date.now(),
      };
      if (!isMountedRef.current) return;
      setSessions(s => [newSession, ...s]);
      setActiveSessionId(newSession.chat_id);
      setDrawerOpen(false);
    } catch (err) {
      // Fallback: create a local-only session if DB fails
      const fallbackId = makeLocalSessionId();
      const newSession = {
        chat_id: fallbackId,
        title: `Local Chat ${sessions.length + 1}`,
        messages: [
          {
            id: makeId('sys'),
            role: 'system',
            text: 'Hello! (local session, DB unavailable)',
            ts: Date.now(),
          },
        ],
        summary: '',
        last_activity: Date.now(),
      };
      if (!isMountedRef.current) return;
      setSessions(s => [newSession, ...s]);
      setActiveSessionId(newSession.chat_id);
      setDrawerOpen(false);
      alert(
        'Warning: Could not create chat session in the database. Using a local-only session.\n\nError: ' +
          err
      );
    }
  };
  const deleteSession = async chat_id => {
    if (isUuid(String(chat_id))) {
      try {
        await fetch(`${getBackendApiBaseUrl()}/chat_sessions/${chat_id}`, { method: 'DELETE' });
      } catch (err) {}
    }
    setSessions(s => s.filter(x => x.chat_id !== chat_id));
    if (activeSessionId === chat_id) {
      setActiveSessionId(null);
    }
  };
  const sendMessage = async (messageText?: string) => {
    const textToSend = messageText || input.trim();
    if (!textToSend) {
      return;
    }
    let sessionId = activeSessionId;
    // If no session, create one and wait for state update
    if (!sessionId) {
      await createNewSession();
      sessionId = activeSessionIdRef.current ?? sessions[0]?.chat_id;
      if (!sessionId) {
        return;
      }
    }
    const userMsg = { id: makeId('u'), role: 'user', text: textToSend, ts: Date.now() };
    setSessions(prev =>
      prev.map(s =>
        String(s.chat_id) === String(sessionId)
          ? { ...s, messages: [...s.messages, userMsg], last_activity: Date.now() }
          : s
      )
    );
    if (!messageText) {
      setInput('');
    }
    setIsTyping(true);
    try {
      if (isUuid(String(sessionId))) {
        await fetch(`${getBackendApiBaseUrl()}/chat_messages/${sessionId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ role: 'user', text: userMsg.text }),
        });
      }
    } catch (e) {
      alert(
        'Warning: Could not save message to the database. This chat is now local-only.\n\nError: ' +
          e
      );
    }
    // Debug log outgoing message
    console.log('[ChatSidebar] Sending WS message:', { type: 'user', payload: userMsg.text });
    if (websocketService.isConnected()) {
      websocketService.sendMessage({
        type: 'user',
        payload: { text: userMsg.text, session_id: sessionId },
        session_id: sessionId,
      });
    } else {
      alert('WebSocket not connected. Message not sent to backend in real time.');
    }
  };
  // Only connect WebSocket once for the app session (not just when sidebar is open)
  useEffect(() => {
    websocketService.connect();
    fetchChatSessions();
    const handleWS = async data => {
      console.log('[ChatSidebar] WS received:', data);

      // Handle echoed user messages from notification buttons
      if (data.type === 'chat_echo') {
        const currentSessionId = activeSessionIdRef.current;
        if (!currentSessionId) return;
        const incomingSessionId = String(data.session_id);
        if (incomingSessionId && incomingSessionId !== String(currentSessionId)) return;
        const userMsg = {
          id: makeId('u'),
          role: 'user',
          text: data.payload?.text || data.payload || '',
          ts: Date.now(),
        };
        setSessions(prev =>
          prev.map(s =>
            String(s.chat_id) === String(currentSessionId)
              ? { ...s, messages: [...s.messages, userMsg], last_activity: Date.now() }
              : s
          )
        );
        setIsTyping(true);
        try {
          if (isUuid(String(currentSessionId))) {
            await fetch(`${getBackendApiBaseUrl()}/chat_messages/${currentSessionId}`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ role: 'user', text: userMsg.text }),
            });
          }
        } catch (err) {}
        return;
      }

      if (data.type !== 'msg') {
        return;
      }

      const incomingSessionId = String(data.session_id);
      if (incomingSessionId && activeSessionIdRef.current && incomingSessionId !== String(activeSessionIdRef.current)) {
        return;
      }

      // const currentSessionId = activeSessionIdRef.current ?? sessions[0]?.chat_id;
      const currentSessionId = activeSessionIdRef.current;
      console.log(currentSessionId);
      if (!currentSessionId) {
        return;
      }
      const textContent =
        typeof data.payload === 'string'
          ? data.payload
          : data.payload.text || JSON.stringify(data.payload);

      // Don't display raw plan JSON in chat — it belongs in the playground canvas
      const trimmed = textContent.trim();
      if (trimmed.startsWith('{') && trimmed.includes('"steps"')) {
        try {
          const parsed = JSON.parse(trimmed);
          if (parsed.steps && Array.isArray(parsed.steps)) {
            return;
          }
        } catch (_) { /* not valid JSON, show as normal */ }
      }

      const assistantMsg = {
        id: makeId('a'),
        role: 'assistant',
        text: textContent,
        ts: Date.now(),
      };
      console.log(assistantMsg);
      setSessions(prev => {
        const match = prev.find(s => String(s.chat_id) === String(currentSessionId));
        if (match) {
          return prev.map(s =>
            String(s.chat_id) === String(currentSessionId)
              ? { ...s, messages: [...s.messages, assistantMsg], last_activity: Date.now() }
              : s
          );
        }
        // No session exists yet — create one with this message
        return [{
          chat_id: currentSessionId,
          title: 'Research Session',
          messages: [assistantMsg],
          summary: '',
          last_activity: Date.now(),
        }, ...prev];
      });
      setIsTyping(false);
      try {
        if (isUuid(String(currentSessionId))) {
            await fetch(`${getBackendApiBaseUrl()}/chat_messages/${currentSessionId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ role: 'assistant', text: assistantMsg.text }),
          });
        }
      } catch (err) {}
    };
    const unsubscribe = websocketService.subscribe(handleWS);
    return () => {
      unsubscribe();
    };
  }, []);
  useEffect(() => {
    console.log(activeSessionId);
    if (activeSessionId) {
      fetchMessagesForSession(activeSessionId);
    }
  }, [activeSessionId]);
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isTyping]);

  // Detect plan approval requests and notify parent
  useEffect(() => {
    if (messages && messages.length > 0 && onPlanApprovalNeeded) {
      const lastMessage = messages[messages.length - 1];
      if (
        lastMessage.role === 'assistant' &&
        lastMessage.text &&
        lastMessage.text.includes('Review the pipeline plan')
      ) {
        onPlanApprovalNeeded();
      }
    }
  }, [messages, onPlanApprovalNeeded]);
  if (!isOpen) {
    return null;
  }
  return (
    <div
      className={`relative flex h-full min-w-[340px] max-w-[min(90vw,760px)] flex-col border-l border-[#1a2a3a] bg-[#0f1923] shadow-[-10px_0_25px_-5px_rgba(0,0,0,0.4)] ${isResizing ? 'select-none' : ''}`}
      style={{ width: `${sidebarWidth}px` }}
    >
      <button
        type="button"
        className="absolute left-[-4px] top-0 z-50 h-full w-2 cursor-col-resize border-0 bg-transparent"
        aria-label="Resize chat sidebar"
        onMouseDown={handleResizeStart}
      />
      <div className="flex h-14 items-center justify-between border-b border-[#1a2a3a] bg-[#0a1520] px-5">
        <div className="flex items-center gap-2.5 text-[15px] font-semibold text-[#e0f0ff]">
          {renderIcon(MessageSquare, 16)} Assistant
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => setDrawerOpen(!drawerOpen)}
            title="History"
            className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-lg border border-transparent text-[#b0d4ea] transition-all hover:border-[#1e3248] hover:bg-[#0d1e2e] hover:text-[#e0f0ff]"
          >
            {renderIcon(History, 16)}
          </button>
          <button
            onClick={() => createNewSession()}
            title="New Chat"
            className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-lg border border-transparent text-[#b0d4ea] transition-all hover:border-[#1e3248] hover:bg-[#0d1e2e] hover:text-[#e0f0ff]"
          >
            {renderIcon(Plus, 16)}
          </button>
          <button
            onClick={onClose}
            className="ml-2 inline-flex h-[30px] w-[30px] items-center justify-center rounded-lg border border-transparent text-[#b0d4ea] transition-all hover:border-[#1e3248] hover:bg-[#0d1e2e] hover:text-[#e0f0ff]"
          >
            {renderIcon(ChevronRight, 18)}
          </button>
        </div>
      </div>
      {drawerOpen && (
        <div className="absolute inset-0 top-14 z-20 overflow-y-auto border-l border-[#1a2a3a] bg-[#0a1520]/95 backdrop-blur">
          <div className="p-4">
            <div className="mb-2.5 text-[11px] font-semibold uppercase tracking-wider text-[#3d5a72]">Chat History</div>
            {sessions.map(s => (
              <div
                key={s.chat_id}
                onClick={() => {
                  setActiveSessionId(s.chat_id);
                  setDrawerOpen(false);
                }}
                className={`group mb-1.5 flex cursor-pointer items-center justify-between rounded-[10px] border p-2.5 transition-all ${s.chat_id === activeSessionId ? 'border-[#2a5a8a] bg-[rgba(26,74,122,0.2)]' : 'border-transparent hover:border-[#1e3248] hover:bg-[#0d1e2e]'}`}
              >
                <span className="truncate text-sm text-[#dadada]">{s.title}</span>
                <button
                  onClick={e => {
                    e.stopPropagation();
                    deleteSession(s.chat_id);
                  }}
                  className="inline-flex h-[22px] w-[22px] items-center justify-center rounded-md text-[#ef4444] opacity-0 transition-all group-hover:opacity-100"
                >
                  {renderIcon(Trash2, 12)}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="flex flex-1 flex-col gap-5 overflow-y-auto bg-[#0f1923] p-6">
        {messages.length === 0 && (
          <div className="mt-10 text-center text-[13px] text-[#3d5a72]">Start a new conversation...</div>
        )}
        {messages.map(msg => (
          <div
            key={msg.id}
            className={`flex max-w-full flex-col gap-1 ${msg.role === 'user' ? 'items-end' : 'items-start'}`}
          >
            <div className={`max-w-[88%] rounded-[18px] border p-3 text-[14.5px] leading-6 shadow ${msg.role === 'user' ? 'border-[#2a5a8a] bg-[linear-gradient(135deg,#1a4a7a_0%,#0d3560_100%)] text-[#e0f0ff]' : msg.role === 'assistant' ? 'border-[#1e3248] bg-[#111d29] text-[#8ab8cc]' : 'border-[#1a2a3a] bg-[#0d1e2e] text-[#b0d4ea]'}`}>
              <div>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text || ''}</ReactMarkdown>
              </div>
            </div>
            <span className="mx-1 text-[11px] text-[#2d4a5e]">
              {new Date(msg.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          </div>
        ))}
        {showThinking && (
          <div className="flex items-center gap-2 p-2 text-[13px] text-[#3d5a72]">
            <ActivityIcon size={14} className="animate-spin text-[#5acce6]" />
            <span className="italic">CUBE is thinking...</span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>
      <div className="border-t border-[#1a2a3a] bg-[#0a1520] px-5 py-4">
        <div className="flex items-end rounded-xl border border-[#1e3248] bg-[#0d1e2e] px-3 py-2">
          <input
            type="text"
            className="flex-1 bg-transparent py-1 text-[14.5px] text-[#e0f0ff] outline-none placeholder:text-[#3d5a72]"
            placeholder="Type a message..."
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && sendMessage()}
          />
          <button
            onClick={sendMessage}
            className="inline-flex h-[34px] w-[34px] items-center justify-center rounded-[10px] border border-[#2a5a8a] bg-[linear-gradient(135deg,#1a4a7a_0%,#0d3560_100%)] text-[#5acce6] transition-all hover:border-[#5acce6] hover:text-white disabled:cursor-not-allowed disabled:opacity-45"
            disabled={!input.trim() || isTyping}
          >
            {renderIcon(Send, 16)}
          </button>
        </div>
      </div>
    </div>
  );
};
export default ChatSidebar;
