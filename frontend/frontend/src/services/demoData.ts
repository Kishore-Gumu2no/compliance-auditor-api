// Demo/Sample Data for FairPay Engine
// Used for Vercel deployment when backend is not available

export interface SampleAuditResult {
  id: string;
  partnerName: string;
  shiftDate: string;
  hoursWorked: number;
  rate: number;
  actualPayout: number;
  expectedPayout: number;
  discrepancy: number;
  status: string;
  platform: string;
  receiptId: string;
  auditDetails: {
    minimumWage: number;
    complianceStatus: string;
    explanation: string;
  };
}

export const SAMPLE_AUDIT_RESULTS: SampleAuditResult[] = [
  {
    id: 'audit-001',
    partnerName: 'Bobby',
    shiftDate: '2024-01-15',
    hoursWorked: 8.5,
    rate: 180,
    actualPayout: 1400,
    expectedPayout: 1530,
    discrepancy: -130,
    status: 'Underpaid (Below Minimum Wage)',
    platform: 'Zomato',
    receiptId: 'a1b2c3d4e5f6789012345678901234567890abcdef1234567890abcdef123456',
    auditDetails: {
      minimumWage: 200,
      complianceStatus: 'NON_COMPLIANT',
      explanation: 'The hourly rate of ₹180 is below the Karnataka 2024 minimum wage of ₹200 for delivery partners. Expected payout for 8.5 hours at minimum wage: ₹1,700. Actual payout: ₹1,400. Discrepancy: -₹300.',
    },
  },
  {
    id: 'audit-002',
    partnerName: 'Bobby',
    shiftDate: '2024-01-20',
    hoursWorked: 6.0,
    rate: 220,
    actualPayout: 1320,
    expectedPayout: 1320,
    discrepancy: 0,
    status: 'Paid correctly',
    platform: 'Swiggy',
    receiptId: 'f6e5d4c3b2a1098765432109876543210987654321098765432109876543210',
    auditDetails: {
      minimumWage: 200,
      complianceStatus: 'COMPLIANT',
      explanation: 'The hourly rate of ₹220 meets the Karnataka 2024 minimum wage of ₹200. Hours worked: 6. Expected payout: ₹1,320. Actual payout: ₹1,320. No discrepancy found.',
    },
  },
  {
    id: 'audit-003',
    partnerName: 'Bobby',
    shiftDate: '2024-02-01',
    hoursWorked: 10.0,
    rate: 190,
    actualPayout: 1800,
    expectedPayout: 1900,
    discrepancy: -100,
    status: 'Underpaid (Below Minimum Wage)',
    platform: 'Uber Eats',
    receiptId: '9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8',
    auditDetails: {
      minimumWage: 200,
      complianceStatus: 'NON_COMPLIANT',
      explanation: 'The hourly rate of ₹190 is below the Karnataka 2024 minimum wage of ₹200 for delivery partners. Expected payout for 10 hours at minimum wage: ₹2,000. Actual payout: ₹1,800. Discrepancy: -₹200.',
    },
  },
  {
    id: 'audit-004',
    partnerName: 'Bobby',
    shiftDate: '2024-02-10',
    hoursWorked: 7.5,
    rate: 250,
    actualPayout: 2000,
    expectedPayout: 1875,
    discrepancy: 125,
    status: 'Overpaid',
    platform: 'Zomato',
    receiptId: 'b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c',
    auditDetails: {
      minimumWage: 200,
      complianceStatus: 'COMPLIANT',
      explanation: 'The hourly rate of ₹250 exceeds the Karnataka 2024 minimum wage of ₹200. Hours worked: 7.5. Expected payout at agreed rate: ₹1,875. Actual payout: ₹2,000. Platform paid ₹125 above agreed rate.',
    },
  },
];

export const SAMPLE_PLATFORM_EARNINGS = {
  Zomato: 4200,
  Swiggy: 3800,
  'Uber Eats': 2100,
};

export function getDemoAuditResult(index: number = 0): SampleAuditResult {
  return SAMPLE_AUDIT_RESULTS[index % SAMPLE_AUDIT_RESULTS.length];
}

export function getAllDemoAuditResults(): SampleAuditResult[] {
  return SAMPLE_AUDIT_RESULTS;
}

export function formatCurrency(amount: number): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(amount);
}

export function getStatusColor(status: string): string {
  if (status.toLowerCase().includes('underpaid')) return '#ef4444';
  if (status.toLowerCase().includes('overpaid')) return '#f59e0b';
  return '#10b981';
}