import member01 from './assets/team/member-01.jpeg';
import member02 from './assets/team/member-02.jpeg';
import member03 from './assets/team/member-03.jpeg';
import member04 from './assets/team/member-04.jpeg';

// Photos follow the supplied attachment order. Role assignments are
// sample placeholders; the bios are proposed copy, not verified credentials.
export const team: { name: string; role: string; description: string; photo: string | null; initials: string; photoPosition?: string }[] = [
  { name: 'Luka', role: 'Data & backend', description: 'Develops the infrastructure connecting astronomical archives to the explorer. Focuses on reliable data access, efficient processing, and maintainable APIs that turn complex observations into an accessible research experience.', photo: member01, initials: 'L', photoPosition: '50% 35%' },
  { name: 'Umair', role: 'Science & statistics', description: 'Shapes the analytical methods behind image comparison and motion measurement. Focuses on uncertainty, reproducible workflows, and careful validation to help distinguish meaningful changes from noise and observational artifacts.', photo: member02, initials: 'U', photoPosition: '50% 32%' },
  { name: 'Shayan', role: 'Design & frontend', description: 'Transforms scientific workflows into intuitive, engaging interfaces. Brings together interaction design, visual storytelling, and frontend engineering to make astronomical data approachable without losing the detail that matters.', photo: member03, initials: 'S', photoPosition: '50% 35%' },
  { name: 'Agamusa', role: 'Mission & research', description: 'Connects the mission context, research priorities, and project narrative. Focuses on translating scientific questions into practical goals, coordinating validation, and communicating the team’s methods and findings with clarity.', photo: member04, initials: 'A', photoPosition: '50% 35%' },
];
