import { NavigateFunction } from 'react-router-dom';

type History = {
  navigate: NavigateFunction;
};

export const history: History = {
  navigate: null,
};
