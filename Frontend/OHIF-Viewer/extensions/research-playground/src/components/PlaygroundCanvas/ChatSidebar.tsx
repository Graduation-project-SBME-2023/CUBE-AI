import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useSnackbar } from '@ohif/ui';
import {
  MessageSquare,
  History,
  Plus,
  ChevronRight,
  Trash2,
  Send,
  Activity as ActivityIcon,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import websocketService from '../../services/websocket';
import { getBackendApiBaseUrl } from '../../utils/apiBase';
import { submitSessionInput } from '../../services/api';

const makeId = (prefix = 'id') => `${prefix}_${Math.random().toString(36).slice(2, 9)}`;
const renderIcon = (Icon, size) => React.createElement(Icon, { size, 'aria-hidden': true });
const isUuid = (value: unknown) =>
  typeof value === 'string' &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);

// current rejects non-UUID session ids at every API and WebSocket boundary. The
// local-only id this file used to mint on a failed create produced a whole
// pipeline run that no database row described and that could never be
// recovered — a failed session create is a visible error, not a phantom run.

// A session owns many chats. Messages belong to a CHAT, so the sidebar
// resolves the session's first chat once and posts there; the chat id is never
// the session id.
const resolveChatId = async (sessionId: string): Promise<string | null> => {
  try {
    const response = await fetch(
      `${getBackendApiBaseUrl()}/chats/session/${encodeURIComponent(sessionId)}?limit=1`
    );
    if (!response.ok) {
      return null;
    }
    const chats = await response.json();
    if (Array.isArray(chats) && chats.length > 0) {
      return String(chats[0].chat_id);
    }
    const created = await fetch(
      `${getBackendApiBaseUrl()}/chats/session/${encodeURIComponent(sessionId)}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: 'Chat' }),
      }
    );
    if (!created.ok) {
      return null;
    }
    return String((await created.json()).chat_id);
  } catch (err) {
    console.error('Could not resolve the session chat', err);
    return null;
  }
};

const ChatSidebar = ({
  isOpen,
  onClose,
  initialUserMessage,
  externalSessionId,
  sendInitialMessage = false,
  onPlanApprovalNeeded,
  isChatLocked = false,
}) => {
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [input, setInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(420);
  const [isResizing, setIsResizing] = useState(false);
  const [sessionToDelete, setSessionToDelete] = useState<any | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [maxSidebarWidth, setMaxSidebarWidth] = useState(() =>
    Math.max(340, Math.min(760, Math.floor(window.innerWidth * 0.55)))
  );
  const snackbar = useSnackbar();

  const resizeStateRef = useRef({ startX: 0, startWidth: 420 });
  const messagesEndRef = useRef(null);
  const activeSessionIdRef = useRef(null);
  // The chat that receives messages for the active session.
  const activeChatIdRef = useRef<string | null>(null);
  const isMountedRef = useRef(true);
  // B2: prevent the initial message from being sent more than once
  const initialMessageSentRef = useRef(false);
  // B1: buffer WS messages that arrive before the session is ready
  const earlyMessageBufferRef = useRef<any[]>([]);

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
          return [
            {
              chat_id: externalSessionId,
              title: 'Research Session',
              messages: initialUserMessage
                ? [{ id: makeId('init'), role: 'user', text: initialUserMessage, ts: Date.now() }]
                : [],
              summary: '',
              last_activity: Date.now(),
            },
          ];
        }
        // Already in list — keep only this session so history stays scoped
        return prev.filter(s => String(s.chat_id) === String(externalSessionId));
      });
      // Refresh just this session's metadata from the DB (title, summary, etc.)
      fetchChatSessions(String(externalSessionId));
    }
    // B2: guard against double-send — use sessionStorage to survive remounts and HMR
    if (sendInitialMessage && externalSessionId) {
      const sentKey = `cube_initial_msg_sent_${externalSessionId}`;
      if (sessionStorage.getItem(sentKey) === 'true') {
        return;
      }
      sessionStorage.setItem(sentKey, 'true');

      console.log('[HypothesisFlow][ChatSidebar] Sending initial message to chat backend', {
        externalSessionId,
        preview: (initialUserMessage || '').slice(0, 120),
      });
      if (!isUuid(String(externalSessionId))) {
        return;
      }
      resolveChatId(String(externalSessionId))
        .then(chatId => {
          if (!chatId) {
            throw new Error('No chat for this session');
          }
          activeChatIdRef.current = chatId;
          return fetch(`${getBackendApiBaseUrl()}/chat_messages/${chatId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ role: 'user', text: initialUserMessage }),
          });
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
  }, [externalSessionId, sendInitialMessage, initialUserMessage]);

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

  const fetchChatSessions = async (scopedSessionId?: string) => {
    // When the playground is scoped to a specific research session, only fetch that
    // session's metadata — never pull the global list, which would pollute the
    // history drawer with sessions from completely different studies.
    if (scopedSessionId && isUuid(String(scopedSessionId))) {
      try {
        const r = await fetch(`${getBackendApiBaseUrl()}/sessions/${scopedSessionId}`);
        if (!r.ok) return;
        const s = await r.json();
        if (!isMountedRef.current) return;
        setSessions(prev => {
          const idx = prev.findIndex(x => String(x.chat_id) === String(s.session_id));
          const entry = {
            chat_id: s.session_id,
            title: s.title || s.prompt || `Session ${s.session_id}`,
            messages: idx !== -1 && prev[idx].messages.length > 0 ? prev[idx].messages : [],
            summary: '',
            last_activity: s.updated_at ? new Date(s.updated_at).getTime() : 0,
          };
          if (idx === -1) return [entry, ...prev];
          return prev.map((x, i) => (i === idx ? entry : x));
        });
      } catch (err) {}
      return;
    }

    // No scoped session — fetch the full global list (used when no externalSessionId)
    try {
      const data = [];
      for (let skip = 0; ; skip += 100) {
        const r = await fetch(`${getBackendApiBaseUrl()}/sessions/?skip=${skip}&limit=100`);
        if (!r.ok) {
          throw new Error(`HTTP ${r.status}`);
        }
        const page = await r.json();
        data.push(...page);
        if (page.length < 100) break;
      }
      const mapped = (data || []).map(s => ({
        chat_id: s.session_id,
        title: s.title || s.prompt || `Session ${s.session_id}`,
        messages: [],
        summary: '',
        last_activity: s.updated_at ? new Date(s.updated_at).getTime() : 0,
      }));
      if (!isMountedRef.current) return;
      setSessions(prev => {
        const merged = [...prev];
        for (const dbSession of mapped) {
          const idx = merged.findIndex(s => String(s.chat_id) === String(dbSession.chat_id));
          if (idx === -1) {
            merged.push(dbSession);
          } else {
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
  const fetchMessagesForSession = async session_id => {
    if (!isUuid(String(session_id))) {
      return;
    }
    // Messages hang off a chat, not off the session.
    const chat_id = await resolveChatId(String(session_id));
    if (!chat_id) {
      return;
    }
    activeChatIdRef.current = chat_id;
    try {
      const msgs = [];
      for (let skip = 0; ; skip += 100) {
        const res = await fetch(
          `${getBackendApiBaseUrl()}/chat_messages/${chat_id}?skip=${skip}&limit=100`
        );
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const page = await res.json();
        msgs.push(...page);
        if (page.length < 100) break;
      }
      const dbMessages = msgs.map(m => ({
        id: `db_${m.message_id}`,
        role: m.role,
        text: m.text,
        ts: new Date(m.timestamp).getTime(),
      }));
      if (!isMountedRef.current) return;
      setSessions(prev =>
        prev.map(s => {
          if (String(s.chat_id) !== String(session_id)) return s;
          // Merge: use DB messages as base, append any local-only messages not in DB
          const dbIds = new Set(dbMessages.map(m => m.id));
          const dbTexts = new Set(dbMessages.map(m => m.text.trim()));
          const localOnly = s.messages.filter(
            m => !dbIds.has(m.id) && !m.id.startsWith('db_') && !dbTexts.has(m.text.trim())
          );
          return { ...s, messages: [...dbMessages, ...localOnly] };
        })
      );
    } catch (err) {}
  };
  // B4: returns the new session ID directly so callers don't race on ref/state.
  //
  // A session is created WITH the user's own words — `prompt` is required
  // and immutable, and the session's empty plan is created in the same
  // server-side transaction. So a session cannot be created by pressing "New
  // chat"; it is created when the first message is sent, and that message IS
  // the prompt.
  const createNewSession = async (prompt: string): Promise<string | null> => {
    const trimmed = (prompt || '').trim();
    if (!trimmed) {
      snackbar.show({
        title: 'Cannot start a session',
        message: 'Type your research question first — it is saved as the session prompt.',
        type: 'warning',
        duration: 5000,
      });
      return null;
    }

    try {
      const r = await fetch(`${getBackendApiBaseUrl()}/sessions/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: trimmed, title: trimmed.slice(0, 60) }),
      });
      if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
      }
      const data = await r.json();
      const sessionId = String(data.session_id);

      const chatId = await resolveChatId(sessionId);
      if (!chatId) {
        throw new Error('Session created but its chat could not be resolved');
      }
      activeChatIdRef.current = chatId;

      const newSession = {
        chat_id: sessionId,
        title: data.title || trimmed.slice(0, 60),
        messages: [
          {
            id: makeId('sys'),
            role: 'system',
            text: 'Hello! I am your research assistant.',
            ts: Date.now(),
          },
        ],
        summary: '',
        last_activity: Date.now(),
      };
      if (!isMountedRef.current) return null;
      setSessions(s => [newSession, ...s]);
      setActiveSessionId(sessionId);
      setDrawerOpen(false);
      return sessionId;
    } catch (err) {
      // No local-only fallback: a run that the database never recorded cannot
      // be resumed, cannot be reported on, and silently loses the user's work.
      console.error('Failed to create session', err);
      snackbar.show({
        title: 'Could not start the session',
        message:
          'The application API did not accept the new research session. Nothing was started; check the API and try again.',
        type: 'error',
        duration: 8000,
      });
      return null;
    }
  };

  const confirmDeleteSession = async () => {
    if (!sessionToDelete) return;
    setIsDeleting(true);
    const chat_id = sessionToDelete.chat_id;
    try {
      if (isUuid(String(chat_id))) {
        // Normal removal is archiving; provenance blocks a hard delete.
        await fetch(`${getBackendApiBaseUrl()}/sessions/${chat_id}/archive`, {
          method: 'POST',
        });
      }
      setSessions(s => s.filter(x => x.chat_id !== chat_id));
      if (activeSessionId === chat_id) {
        setActiveSessionId(null);
      }
    } catch (err) {
      console.error('Error deleting session', err);
    } finally {
      setIsDeleting(false);
      setSessionToDelete(null);
    }
  };
  const sendMessage = async (messageText?: string) => {
    const textToSend = messageText || input.trim();
    if (!textToSend) {
      return;
    }
    let sessionId = activeSessionId;
    // B4: use returned ID directly — avoids React state/ref race condition
    if (!sessionId) {
      // The first message IS the session prompt.
      const newId = await createNewSession(textToSend);
      if (!newId) return;
      sessionId = newId;
    }
    const userMsg = {
      id: makeId('u'),
      id_uuid:
        typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
          ? crypto.randomUUID()
          : undefined,
      role: 'user',
      text: textToSend,
      ts: Date.now(),
    };
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
      const chatId =
        activeChatIdRef.current || (await resolveChatId(String(sessionId)));
      if (!chatId) {
        throw new Error('No chat for this session');
      }
      activeChatIdRef.current = chatId;
      const persisted = await fetch(`${getBackendApiBaseUrl()}/chat_messages/${chatId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // The message id is the idempotency key: a retry after a timeout
        // re-sends the same id and gets the same row back.
        body: JSON.stringify({
          message_id: userMsg.id_uuid,
          role: 'user',
          text: userMsg.text,
        }),
      });
      if (!persisted.ok) {
        throw new Error(`HTTP ${persisted.status}`);
      }
    } catch (e) {
      // There is no local-only degradation any more: a message the database
      // does not have is a message the run cannot be reconstructed from.
      snackbar.show({
        title: 'Message not saved',
        message:
          'The application API did not accept this message, so it is not part of the research record. Check the API and send it again.',
        type: 'error',
        duration: 8000,
      });
    }
    // The socket is push-only now (WEBSOCKET_FIX_PLAN.md WS-10): this is the
    // REST replacement for the old `user` WebSocket send. An idempotency key
    // (reusing the same id the chat-persistence POST above already used)
    // makes a retry safe, and a failure here is reported instead of a
    // pre-WS-10 silent drop when the socket happened to be closed (W18).
    try {
      await submitSessionInput(String(sessionId), userMsg.text, userMsg.id_uuid || userMsg.id);
    } catch (e) {
      snackbar.show({
        title: 'Message not delivered',
        message: 'The agent system did not accept this message — it will not be acted on until you resend it.',
        type: 'error',
        duration: 5000,
      });
    }
  };
  // B1: handleWS extracted to useCallback so it can be referenced by the flush effect
  const handleWS = useCallback(
    async (data: any) => {
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
          const chatId =
            activeChatIdRef.current || (await resolveChatId(String(currentSessionId)));
          if (chatId) {
            activeChatIdRef.current = chatId;
            await fetch(`${getBackendApiBaseUrl()}/chat_messages/${chatId}`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ role: 'user', text: userMsg.text }),
            });
          }
        } catch (err) {}
        return;
      }

      if (data.type === 'run_finished') {
        const currentSessionId = activeSessionIdRef.current;
        if (!currentSessionId) return;
        if (data.replaying) return;
        const completionMsg = {
          id: makeId('a'),
          role: 'assistant',
          text: '🎉 **Pipeline complete!** All research agents have finished successfully. You can now review the results in each phase tab.',
          ts: Date.now(),
        };
        setSessions(prev =>
          prev.map(s =>
            String(s.chat_id) === String(currentSessionId)
              ? { ...s, messages: [...s.messages, completionMsg], last_activity: Date.now() }
              : s
          )
        );
        setIsTyping(false);
        try {
          const chatId =
            activeChatIdRef.current || (await resolveChatId(String(currentSessionId)));
          if (chatId) {
            activeChatIdRef.current = chatId;
            await fetch(`${getBackendApiBaseUrl()}/chat_messages/${chatId}`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ role: 'assistant', text: completionMsg.text }),
            });
          }
        } catch (_) {}
        return;
      }

      if (data.type === 'run_stopped') {
        const currentSessionId = activeSessionIdRef.current;
        if (!currentSessionId) return;
        if (data.replaying) return;
        const stoppedMsg = {
          id: makeId('a'),
          role: 'assistant',
          text: '⚠️ **Pipeline stopped.** The execution was cancelled. You can restart the pipeline from the canvas when ready.',
          ts: Date.now(),
        };
        setSessions(prev =>
          prev.map(s =>
            String(s.chat_id) === String(currentSessionId)
              ? { ...s, messages: [...s.messages, stoppedMsg], last_activity: Date.now() }
              : s
          )
        );
        setIsTyping(false);
        try {
          const chatId =
            activeChatIdRef.current || (await resolveChatId(String(currentSessionId)));
          if (chatId) {
            activeChatIdRef.current = chatId;
            await fetch(`${getBackendApiBaseUrl()}/chat_messages/${chatId}`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ role: 'assistant', text: stoppedMsg.text }),
            });
          }
        } catch (_) {}
        return;
      }

      if (data.type !== 'msg') {
        return;
      }

      const incomingSessionId = String(data.session_id);
      if (
        incomingSessionId &&
        activeSessionIdRef.current &&
        incomingSessionId !== String(activeSessionIdRef.current)
      ) {
        return;
      }

      const currentSessionId = activeSessionIdRef.current;
      // B1: if session not ready yet, buffer message and wait for flush effect
      if (!currentSessionId) {
        earlyMessageBufferRef.current.push(data);
        return;
      }

      const textContent =
        typeof data.payload === 'string'
          ? data.payload
          : data.payload.text || JSON.stringify(data.payload);

      // B5: Don't silently drop plan JSON — show a friendly notification instead
      const trimmed = textContent.trim();
      if (trimmed.startsWith('{') && trimmed.includes('"steps"')) {
        try {
          const parsed = JSON.parse(trimmed);
          if (parsed.steps && Array.isArray(parsed.steps)) {
            const planNotifMsg = {
              id: makeId('a'),
              role: 'assistant',
              text: `✅ Pipeline plan received with **${parsed.steps.length} step${parsed.steps.length !== 1 ? 's' : ''}**. Review it in the canvas panel on the left.`,
              ts: Date.now(),
            };
            setSessions(prev =>
              prev.map(s =>
                String(s.chat_id) === String(currentSessionId)
                  ? { ...s, messages: [...s.messages, planNotifMsg], last_activity: Date.now() }
                  : s
              )
            );
            setIsTyping(false);
            return;
          }
        } catch (_) {
          /* not valid JSON — fall through and show as normal message */
        }
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
        return [
          {
            chat_id: currentSessionId,
            title: 'Research Session',
            messages: [assistantMsg],
            summary: '',
            last_activity: Date.now(),
          },
          ...prev,
        ];
      });
      setIsTyping(false);
      try {
        if (isUuid(String(currentSessionId))) {
          const chatIdForMessage =
            activeChatIdRef.current || (await resolveChatId(String(currentSessionId)));
          if (chatIdForMessage) {
            activeChatIdRef.current = chatIdForMessage;
            await fetch(`${getBackendApiBaseUrl()}/chat_messages/${chatIdForMessage}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ role: 'assistant', text: assistantMsg.text }),
            });
          }
        }
      } catch (err) {}
    },
    [snackbar]
  );

  // Subscribe once for the app session (not just when sidebar is open).
  // Connecting is not done here: this effect runs before activeSessionId is
  // necessarily set (ChatSidebar can mount to show the full chat list with
  // no active session at all), and a socket without a session_id is refused
  // server-side (WEBSOCKET_FIX_PLAN.md Principle 2). The effect below drives
  // connect()/reconnect() off activeSessionId instead.
  useEffect(() => {
    // If scoped to a specific session, only fetch that session; otherwise load all
    fetchChatSessions(externalSessionId ? String(externalSessionId) : undefined);
    const unsubscribe = websocketService.subscribe(handleWS);
    return () => {
      unsubscribe();
    };
  }, [handleWS]);

  // Bind this tab's one socket to whichever session is active. Switching
  // sessions closes the old socket and opens a new one (Principle 2) — never
  // silently keep receiving a session we've navigated away from.
  useEffect(() => {
    if (!activeSessionId) return;
    websocketService.connect(String(activeSessionId));
  }, [activeSessionId]);

  // B1: flush buffered early messages once the session becomes available
  useEffect(() => {
    if (!activeSessionId || earlyMessageBufferRef.current.length === 0) return;
    const buffered = [...earlyMessageBufferRef.current];
    earlyMessageBufferRef.current = [];
    console.log(
      `[ChatSidebar] Flushing ${buffered.length} buffered early message(s) for session`,
      activeSessionId
    );
    buffered.forEach(data => handleWS(data));
  }, [activeSessionId, handleWS]);
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
          <img
            src="/assets/icon_white.png"
            alt="CUBE AI Icon"
            className="h-[18px] w-[18px] object-contain"
          />{' '}
          CUBE Assistant
        </div>
        <div className="flex items-center gap-1.5">
          {/* (<button
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
          </button> */}
          <button
            onClick={onClose}
            className="ml-2 inline-flex h-[30px] w-[30px] items-center justify-center rounded-lg border border-transparent text-[#b0d4ea] transition-all hover:border-[#1e3248] hover:bg-[#0d1e2e] hover:text-[#e0f0ff]"
          >
            {renderIcon(ChevronRight, 18)}
          </button>
        </div>
      </div>
      {drawerOpen && (
        <div className="ohif-scrollbar absolute inset-0 top-14 z-20 overflow-y-auto border-l border-[#1a2a3a] bg-[#0a1520]/95 backdrop-blur">
          <div className="p-4">
            <div className="mb-2.5 text-[11px] font-semibold uppercase tracking-wider text-[#3d5a72]">
              Chat History
            </div>
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
                {s.chat_id !== activeSessionId && (
                  <button
                    onClick={e => {
                      e.stopPropagation();
                      setSessionToDelete(s);
                    }}
                    className="inline-flex h-[22px] w-[22px] items-center justify-center rounded-md text-[#ef4444] opacity-0 transition-all hover:bg-[#ef4444]/20 group-hover:opacity-100"
                  >
                    {renderIcon(Trash2, 12)}
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="ohif-scrollbar flex flex-1 flex-col gap-5 overflow-y-auto bg-[#0f1923] p-6">
        {messages.length === 0 && (
          <div className="mt-10 text-center text-[13px] text-[#3d5a72]">
            Start a new conversation...
          </div>
        )}
        {messages.map(msg => (
          <div
            key={msg.id}
            className={`flex max-w-full flex-col gap-1.5 ${msg.role === 'user' ? 'items-end' : 'items-start'}`}
          >
            <div
              className={`max-w-[85%] rounded-[18px] border px-4 py-3 text-[14.5px] leading-[1.6] shadow-md ${msg.role === 'user' ? 'rounded-br-sm border-[#3269a0] bg-[linear-gradient(135deg,#1f578f_0%,#134375_100%)] text-white' : msg.role === 'assistant' ? 'rounded-bl-sm border-[#263c55] bg-[#1a2836] text-[#b0d4ea]' : 'rounded-bl-sm border-[#1a2a3a] bg-[#0d1e2e] text-[#b0d4ea]'}`}
            >
              <div>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text || ''}</ReactMarkdown>
              </div>
            </div>
            <span
              className={`mx-2 text-[11px] font-medium ${msg.role === 'user' ? 'text-[#3d5a72]' : 'text-[#4a6d88]'}`}
            >
              {new Date(msg.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          </div>
        ))}
        {showThinking && (
          <div className="flex items-center gap-2 p-2 text-[13px] text-[#3d5a72]">
            <ActivityIcon
              size={14}
              className="animate-spin text-[#5acce6]"
            />
            <span className="italic">CUBE is thinking...</span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>
      <div className="border-t border-[#1a2a3a] bg-[#0a1520] px-5 py-4">
        <div
          className={`flex items-end rounded-xl border px-3 py-2 transition-colors ${isChatLocked ? 'border-[#3d5a72]/30 bg-[#162432]/50' : 'border-[#1e3248] bg-[#0d1e2e]'}`}
        >
          <input
            type="text"
            className={`flex-1 bg-transparent py-1 text-[14.5px] text-[#e0f0ff] outline-none transition-colors ${isChatLocked ? 'placeholder:text-[#6a8da6]' : 'placeholder:text-[#4a6b85]'}`}
            placeholder={
              isChatLocked ? 'Chat is locked during execution/results' : 'Type a message...'
            }
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !isChatLocked && sendMessage()}
            disabled={isChatLocked}
          />
          <button
            onClick={() => sendMessage()}
            className="disabled:opacity-45 inline-flex h-[34px] w-[34px] items-center justify-center rounded-[10px] border border-[#2a5a8a] bg-[linear-gradient(135deg,#1a4a7a_0%,#0d3560_100%)] text-[#5acce6] transition-all hover:border-[#5acce6] hover:text-white disabled:cursor-not-allowed"
            disabled={!input.trim() || isTyping || isChatLocked}
          >
            {renderIcon(Send, 16)}
          </button>
        </div>
      </div>

      {/* Delete Confirmation Modal for ChatSidebar */}
      {sessionToDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="w-[320px] rounded-xl border border-[#1e3248] bg-[#0f1923] p-5 shadow-2xl">
            <h3 className="mb-2 text-base font-semibold text-white">Delete Chat?</h3>
            <p className="mb-5 text-sm text-[#8ab8cc]">
              Are you sure you want to delete{' '}
              <span className="text-white">"{sessionToDelete.title}"</span>?
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setSessionToDelete(null)}
                disabled={isDeleting}
                className="rounded-lg px-3 py-1.5 text-sm font-medium text-[#8ab8cc] hover:bg-[#1a2a3a] hover:text-white"
              >
                Cancel
              </button>
              <button
                onClick={confirmDeleteSession}
                disabled={isDeleting}
                className="rounded-lg bg-red-500/10 px-3 py-1.5 text-sm font-medium text-red-500 hover:bg-red-500/20 disabled:opacity-50"
              >
                {isDeleting ? 'Deleting...' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
export default ChatSidebar;
