import React, { Component } from 'react';
import BaseTab from './BaseTab';
import { Send } from 'lucide-react';
import { CSSTransition, TransitionGroup } from 'react-transition-group';
import './AgentChat.css'; 

export default class AgentChat extends BaseTab {
  constructor(props) {
    super(props);
    this.state = {
      messages: [
        { sender: 'agent', text: '👋 Hello Doctor, please describe your intial hypothesis for this case.' },
      ],
      input: '',
    };
  }

  // handles sending user messages 
  handleUserMessage = async (text) => {
    if (!text.trim()) return;

    const userMsg = { sender: 'user', text };
    this.setState((prev) => ({
      messages: [...prev.messages, userMsg, { sender: 'agent', text: 'Thinking...' }],
      input: '',
    }));
    const { viewport, displaySet } = this.props.getActiveViewportInfo();
    // agent response 
    const response = await this.mockAgentResponse(text, displaySet.SeriesInstanceUID);
    // the current case is : 
    this.setState((prev) => {
      const updated = [...prev.messages];
      updated.pop(); // remove "Thinking..."
      updated.push({ sender: 'agent', text: response });
      return { messages: updated };
    });
  };


// the agent connection to the server 
  mockAgentResponse = async (text, image) => {
    try {
      const response = await fetch("http://localhost:8150/run", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({  task: text }),
      });

      if (!response.ok) throw new Error(`HTTP error! Status: ${response.status}`);

      const data = await response.json();
      return data['markdown']|| "No response from agent.";
    } catch (error) {
      console.error("Error connecting to backend:", error);
      return " Could not reach the radiologist assistant server.";
    }
  };


  render() {
    const { messages, input } = this.state;

    return (
      <div className="tab">
        <input
          type="radio"
          name="rd"
          id={this.tabId}
          value="AgentChat"
          className="tab-switch"
          onClick={this.onSelectActionTab}
        />

        <label htmlFor={this.tabId} className="tab-label">
          Agent Chat
        </label>

        <div className="tab-content" style={{ color: 'white' }}>

          {/* chat Container */}
          <div className="chat-container">
            <TransitionGroup>
              {messages.map((msg, i) => (
                <CSSTransition key={i} timeout={300} classNames="fade">
                  <div
                    className={`message ${msg.sender === 'user' ? 'user' : 'agent'}`}
                  >
                    {msg.text}
                  </div>
                </CSSTransition>
              ))}
            </TransitionGroup>
          </div>

          {/* input area*/}
          <div className="input-area">
            <input
              type="text"
              placeholder="Type here..."
              value={input}
              onChange={(e) => this.setState({ input: e.target.value })}
              onKeyDown={(e) => e.key === 'Enter' && this.handleUserMessage(input)}
            />
            <button onClick={() => this.handleUserMessage(input)}>
              <Send size={16} />
            </button>
          </div>
        </div>
      </div>
    );
  }
}
