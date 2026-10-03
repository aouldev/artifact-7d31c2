import { type ApolloClient } from '@apollo/client';
import { AttributeInputTypeEnum } from '@dashboard/graphql';

import { AttributeChoicesHandler } from './Handler';

describe('RQ2 behavior admission: BOOLEAN static choices', () => {
  it('returns static options without querying GraphQL', async () => {
    const query = jest.fn();
    const client = { query } as unknown as ApolloClient<unknown>;
    const handler = new AttributeChoicesHandler(client, 'is-visible', '', AttributeInputTypeEnum.BOOLEAN);

    await expect(handler.fetch()).resolves.toEqual([
      { label: 'Yes', value: 'true', slug: 'true' },
      { label: 'No', value: 'false', slug: 'false' },
    ]);
    expect(query).not.toHaveBeenCalled();
  });
});
