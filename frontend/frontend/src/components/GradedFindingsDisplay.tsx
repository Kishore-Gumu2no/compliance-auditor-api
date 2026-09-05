import React from 'react';

interface Finding {
  grade: string; // e.g., 'confirmed_violation', 'potential_issue', etc.
  title: string;
  description: string;
}

interface Props {
  findings: Finding[];
}

export const GradedFindingsDisplay: React.FC<Props> = ({ findings }) => {
  const gradeColors: Record<string, string> = {
    confirmed_violation: '#D32F2F',
    potential_issue: '#F57C00',
    insufficient_evidence: '#1976D2',
    economic_concern: '#388E3C',
  };

  return (
    <section className="graded-findings-section">
      <h3>Graded Findings</h3>
      <ul className="findings-list">
        {findings.map((f, i) => (
          <li key={i} style={{ borderLeft: `4px solid ${gradeColors[f.grade] || '#555'}` }} className="finding-item">
            <strong>{f.title}</strong>
            <p>{f.description}</p>
            <span className="grade-label" style={{ backgroundColor: gradeColors[f.grade] || '#555' }}>{f.grade.replace('_', ' ')}</span>
          </li>
        ))}
      </ul>
    </section>
  );
};
