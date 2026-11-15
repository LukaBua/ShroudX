import lukaBuachidze from './assets/team/luka-buachidze.png';
import yusufJarrar from './assets/team/yusuf-jarrar.jpeg';
import shayanAmir from './assets/team/member-03.jpeg';
import eminAhmadov from './assets/team/emin-ahmadov.jpg';
import aghamusaAzizov from './assets/team/aghamusa-azizov.jpg';

export type TeamMember = {
  name: string;
  role: string;
  description: string;
  photo: string | null;
  initials: string;
  photoPosition?: string;
};

export const team: TeamMember[] = [
  {
    name: 'Luka Buachidze',
    role: 'Team Lead · AEE Department',
    description:
      'Team lead from the AEE Department, guiding overall project direction, coordination, and end-to-end technical delivery.',
    photo: lukaBuachidze,
    initials: 'LB',
    photoPosition: '50% 50%',
  },
  {
    name: 'Yusuf Jarrar',
    role: 'AEE Department',
    description:
      'Team member from the AEE Department, supporting technical research, analysis, and cross-team validation.',
    photo: yusufJarrar,
    initials: 'YJ',
    photoPosition: '50% 25%',
  },
  {
    name: 'Shayan Amir',
    role: 'CENG Department · Frontend',
    description:
      'Represents the CENG Department on frontend engineering, building the explorer interface and visual observation workflows.',
    photo: shayanAmir,
    initials: 'SA',
    photoPosition: '50% 35%',
  },
  {
    name: 'Emin Ahmadov',
    role: 'Coordinates & Flicker Fix',
    description:
      'Worked on target coordinate gathering, astrometric reference alignment, and resolving frame-to-frame brightness flickering.',
    photo: eminAhmadov,
    initials: 'EA',
    photoPosition: '50% 50%',
  },
  {
    name: 'Aghamusa Azizov',
    role: 'Image Processing & SOFI Files',
    description:
      'Worked on image processing, frame cleaning, and handling SOFI files for observational analysis.',
    photo: aghamusaAzizov,
    initials: 'AA',
    photoPosition: '50% 25%',
  },
];
