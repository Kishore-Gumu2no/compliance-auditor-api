import React, { useState } from 'react';
import { simulate_counterfactual, slider_counterfactual, get_counterfactual_history } from '../services/api';

interface Synthesis {
  synthesis: string;
  realist: string;
  optimist: string;
  pessimist: string;
}

interface HistoryItem {
  partner_id: string;
  timestamp: string;
  synthesis: string;
}

export const CounterfactualSimulator: React.FC = () => {
  const [platform, setPlatform] = useState('Zomato');
  const [description, setDescription] = useState('');
  const [adjusted, setAdjusted] = useState(0);
  const [result, setResult] = useState<Synthesis | null>(null);
  const [history, setHistory] = useState<HistoryItem[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<'scenario' | 'slider' | 'history'>('scenario');

  const handleSimulate = async () => {
    if (!description.trim()) return;
    setLoading(true);
    try {
      const res = await simulate_counterfactual({ platform, description });
      setResult(res);
      setActiveTab('scenario');
    } catch (error) {
      console.error('Simulation failed:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSlider = async () => {
    setLoading(true);
    try {
      const res = await slider_counterfactual({ platform, adjusted_value: adjusted });
      setResult(res);
      setActiveTab('slider');
    } catch (error) {
      console.error('Slider simulation failed:', error);
    } finally {
      setLoading(false);
    }
  };

  const loadHistory = async () => {
    setLoading(true);
    try {
      const h = await get_counterfactual_history(platform);
      setHistory(h);
      setActiveTab('history');
    } catch (error) {
      console.error('History load failed:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section id="counterfactual-simulator" className="section counterfactual-section">
      <div className="container">
        <div className="section-header">
          <span className="section-badge">COUNTERFACTUAL</span>
          <h2 className="section-title-large">Counterfactual Simulator</h2>
          <p className="section-subtitle">
            Explore how changes to platform incentives affect your compliance outcomes.
            Simulate scenarios before they happen.
          </p>
        </div>

        <div className="counterfactual-layout">
          {/* Left Panel - Controls */}
          <div className="counterfactual-controls">
            <div className="control-card">
              <h3 className="control-title">Platform</h3>
              <select
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
                className="platform-select"
                disabled={loading}
              >
                <option value="Zomato">Zomato</option>
                <option value="Swiggy">Swiggy</option>
                <option value="Uber Eats">Uber Eats</option>
              </select>
            </div>

            <div className="control-card">
              <h3 className="control-title">Mode</h3>
              <div className="tab-group" role="tablist">
                <button
                  role="tab"
                  aria-selected={activeTab === 'scenario'}
                  onClick={() => setActiveTab('scenario')}
                  className={`tab-button ${activeTab === 'scenario' ? 'active' : ''}`}
                >
                  Scenario
                </button>
                <button
                  role="tab"
                  aria-selected={activeTab === 'slider'}
                  onClick={() => setActiveTab('slider')}
                  className={`tab-button ${activeTab === 'slider' ? 'active' : ''}`}
                >
                  Incentive Slider
                </button>
                <button
                  role="tab"
                  aria-selected={activeTab === 'history'}
                  onClick={loadHistory}
                  className={`tab-button ${activeTab === 'history' ? 'active' : ''}`}
                  disabled={loading}
                >
                  History
                </button>
              </div>
            </div>

            {activeTab === 'scenario' && (
              <div className="control-card">
                <h3 className="control-title">Scenario Description</h3>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Describe the change... e.g., 'Increase peak hour incentive by 15% for weekend shifts'"
                  className="scenario-textarea"
                  rows={4}
                  disabled={loading}
                />
                <button
                  onClick={handleSimulate}
                  disabled={loading || !description.trim()}
                  className="primary-button full-width"
                >
                  {loading ? (
                    <>
                      <span className="loading-spinner" />
                      Simulating...
                    </>
                  ) : (
                    'Run Simulation'
                  )}
                </button>
              </div>
            )}

            {activeTab === 'slider' && (
              <div className="control-card">
                <h3 className="control-title">Adjusted Incentive Value</h3>
                <div className="slider-wrapper">
                  <input
                    type="range"
                    min={0}
                    max={1000}
                    step={10}
                    value={adjusted}
                    onChange={(e) => setAdjusted(parseInt(e.target.value))}
                    className="slider-input"
                    disabled={loading}
                    aria-label="Adjusted incentive value"
                  />
                  <div className="slider-value-display">
                    <span className="slider-label">Current Value</span>
                    <span className="slider-value">{adjusted}</span>
                  </div>
                </div>
                <button
                  onClick={handleSlider}
                  disabled={loading}
                  className="primary-button full-width"
                >
                  {loading ? (
                    <>
                      <span className="loading-spinner" />
                      Applying...
                    </>
                  ) : (
                    'Apply Slider'
                  )}
                </button>
              </div>
            )}

            {activeTab === 'history' && (
              <div className="control-card">
                <h3 className="control-title">Simulation History</h3>
                {history && history.length > 0 ? (
                  <div className="history-list">
                    {history.map((h, i) => (
                      <div key={i} className="history-item">
                        <span className="history-timestamp">{new Date(h.timestamp).toLocaleString()}</span>
                        <span className="history-synthesis">{h.synthesis.substring(0, 120)}...</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="history-empty">
                    <p>No history yet. Run a simulation to see results here.</p>
                    {history && history.length === 0 && (
                      <p className="history-hint">History is empty for this platform.</p>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Right Panel - Results */}
          <div className="counterfactual-results">
            {result ? (
              <div className="result-card-wrapper">
                <div className="result-header">
                  <span className="result-badge">RESULT</span>
                  <span className="result-platform">{platform}</span>
                </div>

                <div className="synthesis-section">
                  <h4 className="result-subtitle">Synthesis</h4>
                  <div className="synthesis-content">
                    <pre>{result.synthesis}</pre>
                  </div>
                </div>

                <div className="personas-grid">
                  <div className="persona-card realist">
                    <div className="persona-header">
                      <span className="persona-icon">⚖️</span>
                      <span className="persona-name">Realist</span>
                    </div>
                    <div className="persona-content">{result.realist}</div>
                  </div>
                  <div className="persona-card optimist">
                    <div className="persona-header">
                      <span className="persona-icon">📈</span>
                      <span className="persona-name">Optimist</span>
                    </div>
                    <div className="persona-content">{result.optimist}</div>
                  </div>
                  <div className="persona-card pessimist">
                    <div className="persona-header">
                      <span className="persona-icon">📉</span>
                      <span className="persona-name">Pessimist</span>
                    </div>
                    <div className="persona-content">{result.pessimist}</div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="results-empty">
                <div className="empty-icon">🔮</div>
                <h4>No Simulation Yet</h4>
                <p>Select a mode and run a simulation to see the three-persona analysis.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
};
