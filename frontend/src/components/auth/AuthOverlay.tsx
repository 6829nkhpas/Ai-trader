import React, { useState } from 'react';
import { useAuthStore } from '../../store/useAuthStore';
import { Lock, Mail, AlertCircle, Loader2, User, Eye, EyeOff } from 'lucide-react';

export default function AuthOverlay() {
  const login = useAuthStore((s) => s.login);
  const signup = useAuthStore((s) => s.signup);
  const [isSignUp, setIsSignUp] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [name, setName] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password) {
      setError('Please fill in all credentials.');
      return;
    }

    if (isSignUp) {
      if (password !== confirmPassword) {
        setError('Passwords do not match.');
        return;
      }
      if (password.length < 6) {
        setError('Password must be at least 6 characters.');
        return;
      }
    }

    setError(null);
    setLoading(true);

    try {
      const res = isSignUp 
        ? await signup(email, password, name)
        : await login(email, password);
        
      if (!res.success) {
        setError(res.error || 'Authentication failed. Please verify credentials.');
      }
    } catch (err: any) {
      setError('Connection refused. Ensure the Strat Ai Auth Service is running.');
    } finally {
      setLoading(false);
    }
  };

  const handleGoogleAuth = () => {
    setLoading(true);
    setError(null);
    // Simulating OAuth authentication flow
    setTimeout(async () => {
      try {
        const res = await login('google.trader@stratai.com', 'google_oauth_bypass_secret_123');
        if (!res.success) {
          setError(res.error || 'Google OAuth failed.');
        }
      } catch (err) {
        setError('Google OAuth service unavailable.');
      } finally {
        setLoading(false);
      }
    }, 1500);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#06080F] px-4">
      {/* Background dot matrix style matching terminal */}
      <div className="absolute inset-0 opacity-10 bg-[radial-gradient(#1E2A3A_1.5px,transparent_1.5px)] [background-size:24px_24px]"></div>

      <div className="relative w-full max-w-md rounded-xl border border-[#1E2A3A] bg-[#0C1017] p-8 shadow-2xl transition-all duration-300">
        
        {/* Header Section */}
        <div className="mb-6 text-center">
          <img 
            src="/strat.svg" 
            alt="Strat Ai Logo" 
            className="mx-auto mb-4 h-14 w-14 object-contain animate-fade-in"
          />
          <h2 className="text-2xl font-black tracking-tight text-white uppercase font-sans">STRAT AI</h2>
          <p className="text-[10px] text-[#94a3b8] mt-1 uppercase tracking-widest font-mono font-bold">
            {isSignUp ? 'Create Trading Account' : 'Institutional Trading Terminal'}
          </p>
        </div>

        {/* Error Notification */}
        {error && (
          <div className="mb-4 flex items-center gap-2.5 rounded-lg bg-red-500/10 border border-red-500/20 px-3.5 py-2.5 text-xs text-red-400">
            <AlertCircle size={16} className="shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Auth Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          
          {isSignUp && (
            <div>
              <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1.5 font-mono">Full Name</label>
              <div className="relative">
                <span className="absolute inset-y-0 left-0 flex items-center pl-3.5 text-slate-500">
                  <User size={14} />
                </span>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Alex Mercer"
                  className="w-full rounded-lg border border-[#1E2A3A] bg-[#06080F] py-2.5 pl-10 pr-4 text-xs text-white placeholder-slate-600 focus:border-[#10b981] focus:ring-1 focus:ring-[#10b981]/30 focus:outline-none transition-all"
                  required={isSignUp}
                />
              </div>
            </div>
          )}

          <div>
            <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1.5 font-mono">Email Address</label>
            <div className="relative">
              <span className="absolute inset-y-0 left-0 flex items-center pl-3.5 text-slate-500">
                <Mail size={14} />
              </span>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="trader@stratai.com"
                className="w-full rounded-lg border border-[#1E2A3A] bg-[#06080F] py-2.5 pl-10 pr-4 text-xs text-white placeholder-slate-600 focus:border-[#10b981] focus:ring-1 focus:ring-[#10b981]/30 focus:outline-none transition-all"
                required
              />
            </div>
          </div>

          <div>
            <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1.5 font-mono">Password</label>
            <div className="relative">
              <span className="absolute inset-y-0 left-0 flex items-center pl-3.5 text-slate-500">
                <Lock size={14} />
              </span>
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••••••"
                className="w-full rounded-lg border border-[#1E2A3A] bg-[#06080F] py-2.5 pl-10 pr-10 text-xs text-white placeholder-slate-600 focus:border-[#10b981] focus:ring-1 focus:ring-[#10b981]/30 focus:outline-none transition-all"
                required
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute inset-y-0 right-0 flex items-center pr-3 text-slate-500 hover:text-[#10b981] cursor-pointer transition-colors"
              >
                {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
          </div>

          {isSignUp && (
            <div>
              <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1.5 font-mono">Confirm Password</label>
              <div className="relative">
                <span className="absolute inset-y-0 left-0 flex items-center pl-3.5 text-slate-500">
                  <Lock size={14} />
                </span>
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="••••••••••••"
                  className="w-full rounded-lg border border-[#1E2A3A] bg-[#06080F] py-2.5 pl-10 pr-10 text-xs text-white placeholder-slate-600 focus:border-[#10b981] focus:ring-1 focus:ring-[#10b981]/30 focus:outline-none transition-all"
                  required={isSignUp}
                />
              </div>
            </div>
          )}

          {/* Primary Action Button - Flat solid Emerald color, no gradients */}
          <button
            type="submit"
            disabled={loading}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-[#10b981] hover:bg-[#059669] py-3 text-xs font-bold text-white transition-all duration-150 mt-6 active:scale-[0.99] disabled:opacity-50 cursor-pointer"
          >
            {loading ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                <span>Processing...</span>
              </>
            ) : (
              <span>{isSignUp ? 'Create Trading Account' : 'Sign In to Strat Ai'}</span>
            )}
          </button>
        </form>

        {/* Divider */}
        <div className="relative my-6">
          <div className="absolute inset-0 flex items-center">
            <div className="w-full border-t border-[#1E2A3A]"></div>
          </div>
          <div className="relative flex justify-center text-[9px] font-bold uppercase tracking-wider">
            <span className="bg-[#0C1017] px-3.5 text-[#64748b] font-mono">Or continue with</span>
          </div>
        </div>

        {/* Google OAuth Button - Styled with border and surface theme */}
        <button
          onClick={handleGoogleAuth}
          disabled={loading}
          className="flex w-full items-center justify-center gap-2.5 rounded-lg border border-[#1E2A3A] bg-[#131922] hover:bg-[#1E2A3A] py-3 text-xs font-bold text-white transition-all duration-150 cursor-pointer disabled:opacity-50"
        >
          <svg className="h-4 w-4 shrink-0" viewBox="0 0 24 24">
            <path
              fill="#EA4335"
              d="M5.266 9.765A7.077 7.077 0 0 1 12 4.909c1.69 0 3.218.6 4.418 1.582L19.91 3C17.782 1.145 15.055 0 12 0 7.354 0 3.373 2.736 1.482 6.727l3.784 3.038z"
            />
            <path
              fill="#4285F4"
              d="M23.49 12.275c0-.868-.077-1.705-.223-2.509H12v4.745h6.445a5.509 5.509 0 0 1-2.39 3.613l3.723 2.882c2.182-2.009 3.445-4.968 3.445-8.73z"
            />
            <path
              fill="#FBBC05"
              d="M5.266 14.235A7.067 7.067 0 0 1 4.909 12c0-.782.136-1.536.357-2.235L1.482 6.727A11.962 11.962 0 0 0 0 12c0 1.927.455 3.745 1.255 5.373l4.01-3.138z"
            />
            <path
              fill="#34A853"
              d="M12 24c3.24 0 5.973-1.077 7.964-2.927l-3.723-2.882c-1.032.691-2.355 1.105-4.241 1.105-3.618 0-6.68-2.445-7.773-5.736L1.255 17.37C3.109 21.264 7.227 24 12 24z"
            />
          </svg>
          <span>Google Accounts</span>
        </button>

        {/* Toggle Mode Footer */}
        <div className="mt-6 text-center text-xs">
          <p className="text-slate-400 font-sans">
            {isSignUp ? 'Already have a trading profile?' : "Don't have a trading profile?"}{' '}
            <button
              onClick={() => {
                setIsSignUp(!isSignUp);
                setError(null);
              }}
              className="font-bold text-[#10b981] hover:text-[#34d399] transition-colors uppercase text-[10px] tracking-wider cursor-pointer"
            >
              {isSignUp ? 'Sign In' : 'Create Account'}
            </button>
          </p>
        </div>
      </div>
    </div>
  );
}
