// Shared API/UI types. Previously these interfaces were declared inline at the
// top of the App monolith; centralising them lets every component and hook share
// one definition of each backend shape (roadmap Phase 6, item 3).

export interface Task {
  id: string;
  time: string;
  title: string;
  completed: boolean;
}

export interface OverNightItem {
  id: string;
  timestamp: string;
  title: string;
  hook: string;
  scriptOutline: string[];
  targetAudience: string;
  ctrScore: string;
}

export interface SkillItem {
  name: string;
  description: string;
  triggers: string[];
  steps: string[];
  learnedAt: string;
}

export interface FileResult {
  fileName: string;
  path: string;
  sizeBytes: number;
}

export interface BuzzcafProject {
  id: string;
  name: string;
  brand: string;
  current_step: string;
  status: string;
  workflow_name: string;
}

export interface ReminderBanner {
  id: string;
  text: string;
  missed?: boolean;
}

export interface BgTask {
  id: string;
  title: string;
  status: string;
  question?: string | null;
  progress?: { ts: string; text: string }[];
  result?: string | null;
}

export type ChatSender = 'user' | 'dexter';

export interface ChatMessage {
  id: string;
  sender: ChatSender;
  text: string;
  action?: string;
}

// A single frame decoded from the /chat/stream SSE body.
export interface StreamEvent {
  type: 'tool_start' | 'token' | 'provider_switched' | 'reply' | 'audio' | 'error' | string;
  payload?: any;
}
