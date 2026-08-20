// API Service Layer for FairPay Engine
// Connects to the backend FastAPI endpoints

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';

export interface AuditResponse {
  status: string;
  ai_analysis: {
    final_status: string;
    receipt_id: string;
  };
}

export interface UploadFilesParams {
  images?: File[];
  pdfs?: File[];
}

export async function uploadEvidence(files: UploadFilesParams): Promise<AuditResponse> {
  const formData = new FormData();

  if (files.images) {
    files.images.forEach(image => {
      formData.append('images', image);
    });
  }

  if (files.pdfs) {
    files.pdfs.forEach(pdf => {
      formData.append('pdfs', pdf);
    });
  }

  const response = await fetch(`${API_BASE_URL}/audit/`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({ detail: 'Upload failed' }));
    throw new Error(errorData.detail || 'Failed to upload evidence');
  }

  return response.json();
}

export interface StreamData {
  platform: string;
  earnings: number;
}

export interface StreamMessage {
  type: 'earnings' | 'error' | 'connected';
  platform?: string;
  earnings?: number;
  message?: string;
}

export type StreamCallback = (message: StreamMessage) => void;

export function createEarningsStream(callback: StreamCallback): WebSocket {
  const ws = new WebSocket(`ws://127.0.0.1:8000/stream`);

  ws.onopen = () => {
    callback({ type: 'connected' });
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.platform && typeof data.earnings === 'number') {
        callback({ type: 'earnings', platform: data.platform, earnings: data.earnings });
      } else {
        // Server sends text confirmation messages
        callback({ type: 'earnings', message: event.data });
      }
    } catch {
      callback({ type: 'earnings', message: event.data });
    }
  };

  ws.onerror = () => {
    callback({ type: 'error', message: 'WebSocket connection error' });
  };

  ws.onclose = () => {
    callback({ type: 'error', message: 'Connection closed. Reconnecting...' });
    // Auto-reconnect after 3 seconds
    setTimeout(() => {
      createEarningsStream(callback);
    }, 3000);
  };

  return ws;
}