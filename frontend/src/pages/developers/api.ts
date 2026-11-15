/**
 * Admin API client helpers for Developer Dashboard.
 * Re-exports from shared frontend/src/api/client.
 */
import {
  getApiKey,
  setApiKey,
  getAuthHeaders,
  apiClient,
  getApiBase,
} from '../../api/client';

export const getAdminApiKey = getApiKey;
export const setAdminApiKey = setApiKey;
export const getAdminHeaders = getAuthHeaders;
export const fetchAdminJson = apiClient;
export { getApiBase, getApiKey, setApiKey, getAuthHeaders, apiClient };
