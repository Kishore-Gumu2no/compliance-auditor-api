import React from 'react';

interface IncentiveModelCardProps {
  platform: string;
  incentive: number; // Percentage or amount
  adherenceRate: number; // 0‑1
}

export const IncentiveModelCard: React.FC<IncentiveModelCardProps> = ({ platform, incentive, adherenceRate }) => {
  const ratePct = (adherenceRate * 100).toFixed(1) + '%';
  return (
    <div className="incentive-card">
      <h3>{platform} Incentive Model</h3>
      <p>Incentive: {incentive}%</p>
      <p>Adherence Rate: {ratePct}</p>
    </div>
  );
};
