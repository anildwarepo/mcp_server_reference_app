// src/lib/api.ts

export const BASE_URL = "http://127.0.0.1:8080";

export const API = {
  sseEvents: `${BASE_URL}/events`,
  startConversation: (user_id: string) => `${BASE_URL}/conversation/${user_id}?sid=${user_id}`,
  signupBusiness: (email: string) => `${BASE_URL}/actor/signup/business/email/${encodeURIComponent(email)}`,
  getIndividualByEmail: (email: string) => `${BASE_URL}/party/individual/email/${encodeURIComponent(email)}`,
};

