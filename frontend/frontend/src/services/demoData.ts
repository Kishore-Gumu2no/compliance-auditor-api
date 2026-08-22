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
    partnerName: 'Harish',
    shiftDate: 'July 1-15, 2026',
    hoursWorked: 120.5,
    rate: 180,
    actualPayout: 19800,
    expectedPayout: 24100,
    discrepancy: -4300,
    status: 'Underpaid (Below Minimum Wage)',
    platform: 'Zomato, Swiggy, Uber Eats',
    receiptId: 'a1b2c3d4e5f6789012345678901234567890abcdef1234567890abcdef123456',
    auditDetails: {
      minimumWage: 200,
      complianceStatus: 'NON_COMPLIANT',
      explanation: 'Harish works across Zomato, Swiggy, and Uber Eats (July 1-15, 2026). The hourly rate of ₹180 is below the Karnataka 2026 minimum wage of ₹200 for delivery partners. Total hours worked: 120.5. Expected payout at minimum wage: ₹24,100. Actual payout: ₹19,800. Discrepancy: -₹4,300.',
    },
  },
  {
    id: 'audit-002',
    partnerName: 'Kavitha',
    shiftDate: 'April 20 to May 14, 2026',
    hoursWorked: 98.0,
    rate: 220,
    actualPayout: 21560,
    expectedPayout: 21560,
    discrepancy: 0,
    status: 'Paid correctly',
    platform: 'Zomato, Swiggy, Uber Eats',
    receiptId: 'f6e5d4c3b2a1098765432109876543210987654321098765432109876543210',
    auditDetails: {
      minimumWage: 200,
      complianceStatus: 'COMPLIANT',
      explanation: 'Kavith works across Zomato, Swiggy, and Uber Eats (April 20 to May 14, 2026). The hourly rate of ₹220 meets the Karnataka 2026 minimum wage of ₹200. Total hours worked: 98. Expected payout: ₹21,560. Actual payout: ₹21,560. No discrepancy found.',
    },
  },
  {
    id: 'audit-003',
    partnerName: 'Rakshith',
    shiftDate: 'June 10-28, 2026',
    hoursWorked: 145.0,
    rate: 190,
    actualPayout: 25800,
    expectedPayout: 29000,
    discrepancy: -3200,
    status: 'Underpaid (Below Minimum Wage)',
    platform: 'Zomato, Swiggy, Uber Eats',
    receiptId: '9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8',
    auditDetails: {
      minimumWage: 200,
      complianceStatus: 'NON_COMPLIANT',
      explanation: 'Rakshith works across Zomato, Swiggy, and Uber Eats (June 10-28, 2026). The hourly rate of ₹190 is below the Karnataka 2026 minimum wage of ₹200 for delivery partners. Total hours worked: 145. Expected payout at minimum wage: ₹29,000. Actual payout: ₹25,800. Discrepancy: -₹3,200.',
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