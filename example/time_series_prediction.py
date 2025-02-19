######################
# So you want to train a Neural CDE model?
# Let's get started!
######################

import math
import torch
import torchcde
import pandas as pd
from sklearn.preprocessing import MinMaxScaler
import numpy as np 


######################
# A CDE model looks like
#
# z_t = z_0 + \int_0^t f_\theta(z_s) dX_s
#
# Where X is your data and f_\theta is a neural network. So the first thing we need to do is define such an f_\theta.
# That's what this CDEFunc class does.
# Here we've built a small single-hidden-layer neural network, whose hidden layer is of width 128.
######################
class CDEFunc(torch.nn.Module):
    def __init__(self, input_channels, hidden_channels):
        ######################
        # input_channels is the number of input channels in the data X. (Determined by the data.)
        # hidden_channels is the number of channels for z_t. (Determined by you!)
        ######################
        super(CDEFunc, self).__init__()
        self.input_channels = input_channels
        self.hidden_channels = hidden_channels

        self.linear1 = torch.nn.Linear(hidden_channels, 128)
        self.linear2 = torch.nn.Linear(128, input_channels * hidden_channels)

    ######################
    # For most purposes the t argument can probably be ignored; unless you want your CDE to behave differently at
    # different times, which would be unusual. But it's there if you need it!
    ######################
    def forward(self, t, z):
        # z has shape (batch, hidden_channels)
        z = self.linear1(z)
        z = z.relu()
        z = self.linear2(z)
        ######################
        # Easy-to-forget gotcha: Best results tend to be obtained by adding a final tanh nonlinearity.
        ######################
        z = z.tanh()
        ######################
        # Ignoring the batch dimension, the shape of the output tensor must be a matrix,
        # because we need it to represent a linear map from R^input_channels to R^hidden_channels.
        ######################
        z = z.view(z.size(0), self.hidden_channels, self.input_channels)
        return z


######################
# Next, we need to package CDEFunc up into a model that computes the integral.
######################
class NeuralCDE(torch.nn.Module):
    def __init__(self, input_channels, hidden_channels, output_channels, interpolation="cubic"):
        super(NeuralCDE, self).__init__()

        self.func = CDEFunc(input_channels, hidden_channels)
        self.initial = torch.nn.Linear(input_channels, hidden_channels)
        self.readout = torch.nn.Linear(hidden_channels, output_channels)
        self.interpolation = interpolation

    def forward(self, coeffs):
        if self.interpolation == 'cubic':
            X = torchcde.CubicSpline(coeffs)
        elif self.interpolation == 'linear':
            X = torchcde.LinearInterpolation(coeffs)
        else:
            raise ValueError("Only 'linear' and 'cubic' interpolation methods are implemented.")

        ######################
        # Easy to forget gotcha: Initial hidden state should be a function of the first observation.
        ######################
        X0 = X.evaluate(X.interval[0])
        z0 = self.initial(X0)

        ######################
        # Actually solve the CDE.
        ######################
        z_T = torchcde.cdeint(X=X,
                              z0=z0,
                              func=self.func,
                              t=X.interval)

        ######################
        # Both the initial value and the terminal value are returned from cdeint; extract just the terminal value,
        # and then apply a linear map.
        ######################
        z_T = z_T[:, 1]
        pred_y = self.readout(z_T)
        return pred_y


######################
# determine the supported device
######################
def get_device():
    if torch.cuda.is_available():
        device = torch.device('cuda:0')
    else:
        device = torch.device('cpu') # don't have GPU 
    return device

######################
# convert a df to tensor to be used in pytorch
######################
def df_to_tensor(df):
    device = get_device()
    return torch.from_numpy(df.values).float().to(device)

def arr_to_tensor(arr):
    device = get_device()
    return torch.from_numpy(arr).float().to(device)

######################
# Prepare and normalize dataframe
######################
def normalize(df):
    features_df = pd.DataFrame()
    features_df['minute'] = df['open_time'].dt.minute
    features_df['hour'] = df['open_time'].dt.hour
    features_df['day_of_week'] = df['open_time'].dt.dayofweek
    features_df['vol'] = df['volume']
    features_df['open'] = df['open']
    features_df['high'] = df['high']
    features_df['low'] = df['low']
    features_df['close'] = df['close']

    scaler = MinMaxScaler(feature_range=(-1, 1))
    scaler = scaler.fit(features_df)

    features_df = pd.DataFrame(scaler.transform(features_df),
                            index=features_df.index,
                            columns=features_df.columns)
    return features_df

######################
# Create sequences
######################
def create_sequences(input_data: pd.DataFrame, target_column, sequence_length):
    sequences = []
    labels = []
    data_size = len(input_data)
    
    for i in range(data_size - sequence_length):
        seq = input_data[i:i+sequence_length]
        label = input_data.iloc[i + sequence_length][target_column]
        sequences.append((seq))
        labels.append((label,))
    return np.array(sequences), np.array(labels)

######################
# Now we need some data.
# Here we have a simple example which generates some spirals, some going clockwise, some going anticlockwise.
######################
def get_data():
    btc_df = pd.read_csv('example/btc_data.csv', parse_dates=['open_time'])
    btc_df_n_t = normalize(btc_df)
    
    # Split training/testing
    train_size = int(len(btc_df_n_t) * .8)
    train_df, test_df = btc_df_n_t[:train_size], btc_df_n_t[train_size + 1:]
    
    # Create sequences
    SEQUENCE_LENGTH = 120
    train_X, train_y = create_sequences(train_df, 'close', SEQUENCE_LENGTH)
    test_X, test_y = create_sequences(test_df, 'close', SEQUENCE_LENGTH)
    
    # Create tensor arrays
    train_X, train_y  = arr_to_tensor(train_X), arr_to_tensor(train_y)
    test_X, test_y  = arr_to_tensor(test_X), arr_to_tensor(test_y)

    return train_X, train_y, test_X, test_y


def main(num_epochs=30):
    train_X, train_y, test_X, test_y = get_data()

    ######################
    # input_channels=3 because we have both the horizontal and vertical position of a point in the spiral, and time.
    # hidden_channels=8 is the number of hidden channels for the evolving z_t, which we get to choose.
    # output_channels=1 because we're doing binary classification.
    ######################
    model = NeuralCDE(input_channels=8, hidden_channels=8, output_channels=1)
    optimizer = torch.optim.Adam(model.parameters())

    ######################
    # Now we turn our dataset into a continuous path. We do this here via Hermite cubic spline interpolation.
    # The resulting `train_coeffs` is a tensor describing the path.
    # For most problems, it's probably easiest to save this tensor and treat it as the dataset.
    ######################
    train_coeffs = torchcde.hermite_cubic_coefficients_with_backward_differences(train_X)

    train_dataset = torch.utils.data.TensorDataset(train_coeffs, train_y)
    train_dataloader = torch.utils.data.DataLoader(train_dataset, batch_size=32)
    for epoch in range(num_epochs):
        for batch in train_dataloader:
            batch_coeffs, batch_y = batch
            pred_y = model(batch_coeffs).squeeze(-1)
            loss = torch.nn.functional.binary_cross_entropy_with_logits(pred_y.unsqueeze(1), batch_y)
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()
        print('Epoch: {}   Training loss: {}'.format(epoch, loss.item()))

    test_coeffs = torchcde.hermite_cubic_coefficients_with_backward_differences(test_X)
    pred_y = model(test_coeffs).squeeze(-1)
    
    # TODO: Modify evaluation for non-binary prediction
    binary_prediction = (torch.sigmoid(pred_y) > 0.5).to(test_y.dtype)
    prediction_matches = (binary_prediction == test_y).to(test_y.dtype)
    proportion_correct = prediction_matches.sum() / test_y.size(0)
    print('Test Accuracy: {}'.format(proportion_correct))


if __name__ == '__main__':
    main()
