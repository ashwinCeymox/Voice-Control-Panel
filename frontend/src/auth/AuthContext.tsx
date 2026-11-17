import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { apiClient, clearApiKey } from '../api/client';

export interface AdminUser {
  email: string;
  must_change_password: boolean;
}

interface AuthContextType {
  user: AdminUser | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (oldPassword: string, newPassword: string) => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<AdminUser | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  const refreshUser = async () => {
    try {
      const data = await apiClient<AdminUser>('/v1/panel/me');
      setUser(data);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refreshUser();
  }, []);

  const login = async (email: string, password: string) => {
    const data = await apiClient<AdminUser>('/v1/panel/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });
    setUser(data);
    // Cleanup any lingering legacy storage keys
    clearApiKey();
  };

  const logout = async () => {
    try {
      await apiClient<{ message: string }>('/v1/panel/logout', {
        method: 'POST',
      });
    } catch {
      // Continue cleanup on failure
    } finally {
      setUser(null);
      clearApiKey();
    }
  };

  const changePassword = async (oldPassword: string, newPassword: string) => {
    await apiClient<{ message: string; must_change_password: boolean }>('/v1/panel/change-password', {
      method: 'POST',
      body: JSON.stringify({
        old_password: oldPassword,
        new_password: newPassword,
      }),
    });
    if (user) {
      setUser({ ...user, must_change_password: false });
    }
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, changePassword, refreshUser }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
